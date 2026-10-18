"""Deteccion y reparacion de MKV sin indice de saltos (Cues) al principio.

Algunos reproductores (ExoPlayer en Android TV, entre otros) solo pueden saltar
dentro de un MKV si el indice de saltos esta referenciado al inicio del archivo.
Si no, al adelantar o atrasar el capitulo reinicia desde el principio. El
arreglo es reempaquetar sin recodificar con el indice al frente.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

CUES_ID = b"\x1c\x53\xbb\x6b"
DEFAULT_PROBE_BYTES = 65536
TMP_SUFFIX = ".seekfix-tmp"


class FixError(Exception):
    """Fallo al reparar un archivo concreto; el original queda intacto."""


def has_front_cues(path: Path, probe_bytes: int = DEFAULT_PROBE_BYTES) -> bool:
    """True si el ID del elemento Cues aparece en el inicio del archivo.

    Es una heuristica: el SeekHead (siempre al principio) enumera donde esta el
    Cues, y si el Cues va al frente su ID tambien queda en esa zona.
    """
    with open(path, "rb") as fh:
        return CUES_ID in fh.read(probe_bytes)


def find_mkv(paths: Iterable[Path]) -> Iterator[Path]:
    """Recorre archivos y carpetas devolviendo los .mkv (sin temporales nuestros)."""
    for root in paths:
        root = Path(root)
        if root.is_file():
            if root.suffix.lower() == ".mkv":
                yield root
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if name.lower().endswith(".mkv"):
                    yield Path(dirpath) / name


@dataclass
class Tools:
    """Como invocar ffmpeg/ffprobe. Permite correrlos dentro de un contenedor.

    ffmpeg y ffprobe son listas de argumentos (prefijo del comando). path_map
    traduce rutas del anfitrion a las que ve la herramienta, por ejemplo
    [("/datos/media", "/media")] cuando ffmpeg vive en un contenedor.
    """

    ffmpeg: list[str] = field(default_factory=lambda: ["ffmpeg"])
    ffprobe: list[str] = field(default_factory=lambda: ["ffprobe"])
    path_map: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_strings(cls, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                     path_maps: Iterable[str] = ()) -> "Tools":
        maps = []
        for item in path_maps:
            host, sep, ct = item.partition("=")
            if not sep or not host or not ct:
                raise ValueError(f"path-map invalido (use HOST=DESTINO): {item!r}")
            maps.append((host.rstrip("/"), ct.rstrip("/")))
        return cls(shlex.split(ffmpeg), shlex.split(ffprobe), maps)

    def translate(self, path: Path) -> str:
        p = str(path)
        for host, ct in self.path_map:
            if p == host or p.startswith(host + "/"):
                return ct + p[len(host):]
        return p

    def probe(self, path: Path) -> tuple[Optional[float], list[tuple[str, str]]]:
        cmd = self.ffprobe + ["-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,codec_name",
                              "-of", "json", self.translate(path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode:
            raise FixError(f"ffprobe fallo: {res.stderr.strip()[-200:]}")
        data = json.loads(res.stdout)
        streams = [(s.get("codec_type", ""), s.get("codec_name", "")) for s in data["streams"]]
        raw = data.get("format", {}).get("duration")
        return (float(raw) if raw else None), streams

    def remux(self, src: Path, dst: Path) -> None:
        cmd = self.ffmpeg + ["-v", "error", "-y", "-i", self.translate(src),
                             "-map", "0", "-map_chapters", "0", "-c", "copy",
                             "-cues_to_front", "1", "-f", "matroska", self.translate(dst)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode:
            raise FixError(f"ffmpeg fallo: {res.stderr.strip()[-300:]}")


@dataclass
class Result:
    path: Path
    status: str  # ok | skip | error | dry-run
    detail: str = ""


def backup_path(backup_dir: Path, src: Path) -> Path:
    """Ruta del respaldo: mismo camino absoluto colgando de backup_dir."""
    return Path(backup_dir) / str(Path(src).resolve()).lstrip("/")


def fix_file(tools: Tools, src: Path, backup_dir: Optional[Path], *,
             dry_run: bool = False, min_age: float = 300.0,
             work_dir: Optional[Path] = None, tolerance: float = 0.5,
             probe_bytes: int = DEFAULT_PROBE_BYTES) -> Result:
    """Repara un archivo. Nunca deja el original en mal estado."""
    src = Path(src)
    try:
        if not src.is_file():
            return Result(src, "skip", "no existe")
        if has_front_cues(src, probe_bytes):
            return Result(src, "skip", "ya tiene indice al frente")
        st = src.stat()
        if min_age and time.time() - st.st_mtime < min_age:
            return Result(src, "skip", "modificado hace poco (descarga en curso?)")
        if dry_run:
            return Result(src, "dry-run", "se repararia")

        tmp_dir = Path(work_dir) if work_dir else src.parent
        tmp = tmp_dir / (src.name + TMP_SUFFIX)
        tmp.unlink(missing_ok=True)
        try:
            tools.remux(src, tmp)
            if not tmp.is_file():
                raise FixError("ffmpeg no genero salida")
            d0, s0 = tools.probe(src)
            d1, s1 = tools.probe(tmp)
            if s0 != s1:
                raise FixError(f"flujos distintos: {s0} vs {s1}")
            if d0 is not None and d1 is not None and abs(d0 - d1) > tolerance:
                raise FixError(f"duracion distinta: {d0:.2f} vs {d1:.2f}")
            if not has_front_cues(tmp, probe_bytes):
                raise FixError("el archivo nuevo no tiene indice al frente")
            new_size = tmp.stat().st_size
            if abs(new_size - st.st_size) > st.st_size * 0.02:
                raise FixError(f"tamano sospechoso: {new_size} vs {st.st_size}")

            if backup_dir is not None:
                bk = backup_path(backup_dir, src)
                if bk.exists():
                    raise FixError(f"ya existe respaldo: {bk}")
                bk.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(bk))
                try:
                    shutil.move(str(tmp), str(src))
                except Exception:
                    shutil.move(str(bk), str(src))
                    raise
            else:
                os.replace(tmp, src)
        finally:
            tmp.unlink(missing_ok=True)

        try:
            os.chmod(src, st.st_mode & 0o7777)
            os.chown(src, st.st_uid, st.st_gid)
        except OSError:
            pass
        return Result(src, "ok", f"{st.st_size / 1e6:.0f} MB -> {src.stat().st_size / 1e6:.0f} MB")
    except FixError as exc:
        return Result(src, "error", str(exc))
    except OSError as exc:
        return Result(src, "error", f"E/S: {exc}")


def restore(backup_dir: Path, *, dry_run: bool = False,
            log: Callable[[str], None] = print) -> list[Result]:
    """Devuelve los originales guardados a su ruta original."""
    out: list[Result] = []
    backup_dir = Path(backup_dir)
    for dirpath, _d, files in os.walk(backup_dir):
        for name in sorted(files):
            bk = Path(dirpath) / name
            target = Path("/") / bk.relative_to(backup_dir)
            if not target.parent.is_dir():
                out.append(Result(target, "skip", "carpeta de destino no existe"))
                continue
            if dry_run:
                out.append(Result(target, "dry-run", "se restauraria"))
                continue
            try:
                shutil.move(str(bk), str(target))
                out.append(Result(target, "ok", "restaurado"))
            except OSError as exc:
                out.append(Result(target, "error", str(exc)))
    return out
