"""Revision y arreglo de nombres para Jellyfin (series/anime y peliculas).

Series: cada episodio debe quedar como "Serie - S01E01.ext" dentro de una
carpeta de temporada. Se deduce temporada y episodio de nombres como "1x01",
"S01.E01", "Temporada 1 Capitulo 1", "1.1" o un numero suelto, y se detecta la
numeracion absoluta (temporada 2 empezando en el 13).

Peliculas: se avisa de las que no tienen año (el año lo pone la persona, no se
inventa) y de las duplicadas, proponiendo cual quitar segun calidad y audio.

Todo se aplica moviendo archivos (nunca borrando), con un registro que permite
deshacer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv"}
SIDE_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".nfo"}
EXTRAS_DIRS = {"extras", "featurettes", "behind the scenes", "deleted scenes",
               "interviews", "scenes", "shorts", "trailers", "other", "clips"}

GOOD_EP = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,4})(?:-?[Ee](\d{1,4}))?")
SEASON_DIR = re.compile(r"^(?:season|temporada|s)\s*0*(\d{1,3})(?!\d)", re.I)
SPECIALS_DIR = re.compile(r"^(?:specials|especiales)$", re.I)
YEAR = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)")
SHOW_SUFFIX = re.compile(r"\s*(\((?:19|20)\d{2}[^)]*\)|\[[^\]]*\])\s*")
TAGS = re.compile(
    r"(?i)(?<![a-z0-9])(\d{3,4}p|4k|uhd|hdr\d*\+?|web[ -]?dl|web[ -]?rip|webdl|bluray|bd|brrip|"
    r"bdrip|hdts|hdrip|dvdrip|x26[45]|h\.?26[45]|hevc|avc|aac\d?(\.\d)?|ac3|ddp?\d?(\.\d)?|"
    r"atmos|10 ?bits?|dual|latino|lat|castellano|cast|multi|subs?|ligera?|pesada|ligero|"
    r"[257]\.[01]|amzn|nf|dsnp|hmax|cr|imax)(?![a-z0-9])")

# Patrones de episodio, del mas fiable al menos.
P_NXM = re.compile(r"(?<!\d)(\d{1,2})[xX](\d{1,4})(?!\d)")
P_S_E = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})[ ._-]+[Ee](\d{1,4})(?!\d)")
P_TEMP_CAP = re.compile(r"(?i)temporada\s*(\d{1,2})\D+?cap[ií]tulo\s*(\d{1,4})")
P_DOT = re.compile(r"(?<!\d)(?<!\d\.)(\d{1,2})\.(\d{1,3})(?!\d)(?!\.\d)")
P_MULTI = re.compile(r"(?<![\dA-Za-z])(\d{1,4})-(\d{1,4})(?![\dA-Za-z])")
P_NUM = re.compile(r"(?<![\dA-Za-z])(\d{1,4})(?![\dA-Za-z])")
P_ID_EP = re.compile(r"\d{2,6}_(\d{1,4})")


@dataclass
class Op:
    """Un movimiento propuesto. kind: rename | trash."""
    src: str
    dst: str
    kind: str = "rename"
    reason: str = ""
    group: str = ""


@dataclass
class Issue:
    """Algo que no se puede arreglar solo; lo decide la persona."""
    path: str
    problem: str
    hint: str = ""
    group: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Plan:
    ops: list[Op] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    shows: list[dict] = field(default_factory=list)  # resumen por serie: temporadas y episodios

    def to_dict(self) -> dict:
        return {"ops": [asdict(o) for o in self.ops],
                "issues": [asdict(i) for i in self.issues],
                "shows": self.shows}


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT


def show_name(folder: str) -> str:
    """'Fire Force (2019)' -> 'Fire Force'; quita años y [tmdbid-...]."""
    return SHOW_SUFFIX.sub(" ", folder).strip() or folder


def season_of_dir(name: str) -> Optional[int]:
    if SPECIALS_DIR.match(name):
        return 0
    m = SEASON_DIR.match(name)
    return int(m.group(1)) if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _clean_stem(stem: str, show: str, tags: bool = True) -> str:
    """Quita del nombre lo que no es numeracion: serie, [grupos], etiquetas."""
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", stem)
    s = re.sub(r"\((?:19|20)\d{2}\)", " ", s)
    words = _norm(show).split()
    if words:
        # Quita el nombre de la serie si va al principio (con puntos o espacios).
        pat = r"^\W*" + r"\W+".join(map(re.escape, words)) + r"(?![a-z0-9])"
        s = re.sub(pat, " ", s, flags=re.I)
    return TAGS.sub(" ", s) if tags else s


@dataclass
class EpGuess:
    season: Optional[int]
    episode: int
    episode_end: Optional[int] = None
    title: str = ""
    absolute_ok: bool = False  # numero suelto: puede ser numeracion absoluta


def _title_after(stem: str, end: int) -> str:
    rest = stem[end:]
    m = re.match(r"\s+-\s+(.+)$", rest)
    if not m:
        return ""
    title = m.group(1).strip()
    return "" if TAGS.search(title) or "[" in title else title


def guess_episode(stem: str, show: str, dir_season: Optional[int]) -> Optional[EpGuess]:
    """Deduce temporada/episodio de un nombre sin SxxEyy. None si no se sabe."""
    stem = re.sub(r"(\.(mkv|mp4|avi|m4v|ts))+$", "", stem, flags=re.I)
    for pat in (P_NXM, P_S_E, P_TEMP_CAP):
        m = pat.search(stem)
        if m:
            return EpGuess(int(m.group(1)), int(m.group(2)), title=_title_after(stem, m.end()))
    # "2.1" en la carpeta de temporada 2 es el episodio 1, no audio 2.1: se mira
    # antes de quitar etiquetas, y solo si coincide con la temporada.
    clean = _clean_stem(stem, show)
    if dir_season is not None:
        raw = re.sub(r"(?i)\d{3,4}p|x26[45]|h\.?26[45]", " ", _clean_stem(stem, show, tags=False))
        for text in (raw, clean):
            m = P_DOT.search(text)
            if m and int(m.group(1)) == dir_season:
                return EpGuess(dir_season, int(m.group(2)))
    m = P_MULTI.search(clean)
    if m and int(m.group(2)) == int(m.group(1)) + 1:
        return EpGuess(dir_season, int(m.group(1)), int(m.group(2)), absolute_ok=True)
    # Descargas tipo "2995_01": identificador de la serie y numero de episodio.
    m = P_ID_EP.fullmatch(stem.strip())
    if m:
        return EpGuess(dir_season, int(m.group(1)), absolute_ok=True)
    nums = [n for n in P_NUM.findall(clean) if not YEAR.fullmatch(n)]
    if len(nums) == 1:
        return EpGuess(dir_season, int(nums[0]), absolute_ok=True)
    return None


def _sidecars(video: Path) -> list[Path]:
    """Subtitulos/nfo con el mismo nombre base: 'ep.srt', 'ep.es.srt'."""
    out = []
    for f in video.parent.iterdir():
        if f == video or f.suffix.lower() not in SIDE_EXT:
            continue
        if f.name.startswith(video.stem + "."):
            out.append(f)
    return out


def _add_rename(plan: Plan, src: Path, dst: Path, reason: str, group: str,
                taken: set[str]) -> None:
    if src == dst:
        return
    if dst.exists() or str(dst) in taken:
        plan.issues.append(Issue(str(src), "conflicto", f"ya existe: {dst.name}", group))
        return
    taken.add(str(dst))
    plan.ops.append(Op(str(src), str(dst), "rename", reason, group))
    for side in _sidecars(src):
        sdst = dst.with_name(dst.stem + side.name[len(src.stem):])
        if not sdst.exists() and str(sdst) not in taken:
            taken.add(str(sdst))
            plan.ops.append(Op(str(side), str(sdst), "rename", "acompaña al video", group))


def plan_series(root: Path, ffprobe: Optional[list[str]] = None) -> Plan:
    """Revisa una biblioteca de series/anime (una carpeta por serie). Con
    ffprobe, en las temporadas con huecos avisa de archivos que duran el doble
    (pueden traer dos episodios)."""
    root = Path(root)
    plan = Plan()
    taken: set[str] = set()
    for f in sorted(root.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            plan.issues.append(Issue(str(f), "archivo suelto en la raiz",
                                     "debe ir dentro de la carpeta de una serie"))
    for show_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "#", "@"))):
        _plan_show(plan, show_dir, taken, ffprobe)
    return plan


def _long_files(ffprobe: list[str], files: list[Path]) -> list[tuple[str, int]]:
    """Archivos que duran 1,7 veces o mas la mediana de la temporada."""
    durs = []
    for f in files:
        d = probe_media(ffprobe, f).duration_min
        if d:
            durs.append((f, d))
    if len(durs) < 3:
        return []
    med = sorted(d for _, d in durs)[len(durs) // 2]
    return [(f.name, d) for f, d in durs if d >= 1.7 * med]


def _plan_show(plan: Plan, show_dir: Path, taken: set[str],
               ffprobe: Optional[list[str]] = None) -> None:
    show = show_name(show_dir.name)
    group = show_dir.name
    # (archivo, temporada de su carpeta, carpeta es raiz de la serie)
    entries: list[tuple[Path, Optional[int], bool]] = []
    for dirpath, dirs, files in os.walk(show_dir):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXTRAS_DIRS and not d.startswith("."))
        d = Path(dirpath)
        dseason = None if d == show_dir else season_of_dir(d.name)
        for name in sorted(files):
            p = d / name
            if is_video(p):
                entries.append((p, dseason, d == show_dir))

    guesses: list[tuple[Path, EpGuess, bool]] = []
    have: dict[int, set[int]] = {}  # temporada -> episodios presentes (ya bien nombrados)
    max_ep = 0
    good_files: list[tuple[Path, int, int, Optional[int]]] = []
    for p, dseason, loose in entries:
        good = GOOD_EP.search(p.name)
        if good:
            s, e = int(good.group(1)), int(good.group(2))
            e2 = int(good.group(3)) if good.group(3) else None
            have.setdefault(s, set()).update(range(e, (e2 or e) + 1))
            good_files.append((p, s, e, e2))
            continue
        g = guess_episode(p.stem, show, dseason if not loose else None)
        if g is None:
            plan.issues.append(Issue(str(p), "no se pudo deducir el episodio",
                                     "renombrar a mano como 'Serie - S01E01'", group))
            continue
        if g.season is None:
            if loose:
                g.season = 1
            else:
                plan.issues.append(Issue(str(p), "temporada desconocida",
                                         f"la carpeta '{p.parent.name}' no indica temporada", group))
                continue
        max_ep = max(max_ep, g.episode_end or g.episode)
        guesses.append((p, g, loose))

    # Numeracion absoluta: la temporada N empieza justo donde acabo la N-1.
    by_season: dict[int, list[int]] = {}
    for p, g, loose in guesses:
        if g.absolute_ok and not loose:
            by_season.setdefault(g.season, []).append(g.episode)
    offset: dict[int, int] = {}
    prev_max = None
    for s in sorted(by_season):
        eps = by_season[s]
        lo, hi = min(eps), max(eps)
        if s >= 2 and lo > 1 and prev_max is not None and lo == prev_max + 1:
            offset[s] = lo - 1
        prev_max = hi
    if offset:
        max_ep = max((g.episode_end or g.episode) - offset.get(g.season, 0) for _, g, _ in guesses)

    pad = 3 if max_ep >= 100 else 2
    for p, g, loose in guesses:
        off = offset.get(g.season, 0) if g.absolute_ok else 0
        ep, ep2 = g.episode - off, (g.episode_end - off if g.episode_end else None)
        code = f"S{g.season:02d}E{ep:0{pad}d}" + (f"-E{ep2:0{pad}d}" if ep2 else "")
        name = f"{show} - {code}" + (f" - {g.title}" if g.title else "") + p.suffix.lower()
        folder = show_dir / f"Season {g.season:02d}" if loose else p.parent
        reason = "numeracion absoluta" if off else ("sin carpeta de temporada" if loose else "nombre sin SxxEyy")
        _add_rename(plan, p, folder / name, reason, group, taken)
        have.setdefault(g.season, set()).update(range(ep, (ep2 or ep) + 1))

    fixed = _fix_mixed_numbering(plan, show, group, good_files, have, taken)
    for p, s, e, e2 in good_files:
        if p not in fixed:
            pad = 3 if max(have.get(s) or {0}) >= 100 else 2
            _tidy_name(plan, p, show, show_dir.name, s, e, e2, pad, group, taken)

    prev_last = None
    for s in sorted(have):
        eps = have[s]
        if s == 0:
            continue
        lo, hi = min(eps), max(eps)
        # Temporada con numeracion absoluta (One Piece S09E311): se cuenta desde
        # donde acabo la anterior, no desde el 1.
        if s >= 2 and lo > 1 and prev_last is not None and prev_last < lo:
            start = prev_last + 1
        elif s >= 2 and lo > 1 and prev_last is None:
            start = lo
        else:
            start = 1
        prev_last = hi
        missing = sorted(set(range(start, hi + 1)) - eps)
        if missing:
            hint = f"temporada {s}: faltan {_ranges(missing)} (tiene {len(eps)}, hasta el {max(eps)})"
            data = {"season": s, "missing": missing, "have": len(eps), "last": max(eps), "show": show}
            if ffprobe:
                # Los ya nombrados como dobles (E026-E027) cuentan como dos: no se avisa.
                files = [p for p, fs, _, e2 in good_files if fs == s and not e2] + \
                        [p for p, g, _ in guesses if g.season == s and not g.episode_end]
                long = _long_files(ffprobe, files)
                if long:
                    hint += "; ojo, duran el doble y pueden traer dos episodios: " + \
                            ", ".join(f"{n} ({d} min)" for n, d in long)
                    data["long_files"] = [n for n, _ in long]
            plan.issues.append(Issue(str(show_dir), "faltan episodios", hint, group, data))
    seasons_of = {p: fs for p, fs, _, _ in good_files}
    seasons_of.update({p: g.season for p, g, _ in guesses})
    _plan_season_dirs(plan, show_dir, seasons_of, group, taken)

    plan.shows.append({"path": str(show_dir), "show": show, "folder": show_dir.name,
                       "seasons": {s: {"have": len(e), "last": max(e)} for s, e in have.items()}})


def _plan_season_dirs(plan: Plan, show_dir: Path, seasons_of: dict[Path, int],
                      group: str, taken: set[str]) -> None:
    """Carpetas de temporada siempre como 'Season 01' ('Season 00' especiales).
    La temporada sale del nombre de la carpeta o, si no lo dice, de sus
    episodios (solo si todos son de la misma). Va al final del plan de la serie:
    primero se renombran los archivos y luego su carpeta."""
    for d in sorted(p for p in show_dir.iterdir() if p.is_dir()):
        if d.name.startswith(".") or d.name.lower() in EXTRAS_DIRS:
            continue
        found = {s for f, s in seasons_of.items() if d in f.parents}
        season = season_of_dir(d.name)
        if season is None or (found and found != {season}):
            if len(found) != 1:
                continue
            season = found.pop()
        target = d.with_name(f"Season {season:02d}")
        if target.name == d.name:
            continue
        if target.exists() or str(target) in taken or \
                any(t.startswith(str(target) + "/") for t in taken):
            plan.issues.append(Issue(str(d), "carpeta de temporada repetida",
                                     f"ya existe «{target.name}»: junta las carpetas a mano", group))
            continue
        taken.add(str(target))
        plan.ops.append(Op(str(d), str(target), "rename", "formato de temporada", group))


def _episode_title(stem: str, end: int) -> str:
    """Titulo que va despues de SxxEyy: ' - Titulo' o '.Titulo.Con.Puntos.1080p'."""
    rest = re.sub(r"\[[^\]]*\]|\{[^}]*\}|\([^)]*\)", " ", stem[end:])
    rest = re.sub(r"(\.(mkv|mp4|avi|m4v|ts))+$", "", rest, flags=re.I)
    if " " not in rest.strip(" .-_"):
        rest = rest.replace(".", " ").replace("_", " ")
    t = TAGS.search(rest)
    if t:
        rest = rest[:t.start()]
    rest = re.sub(r"\s+", " ", rest).strip(" .-_")
    return rest if re.search(r"[A-Za-zÀ-ÿ]{2}", rest) else ""


def _tidy_name(plan: Plan, p: Path, show: str, folder: str, s: int, e: int, e2: Optional[int],
               pad: int, group: str, taken: set[str]) -> None:
    """Deja un nombre que ya tiene SxxEyy como 'Serie - S01E01[ - Titulo].ext'."""
    m = GOOD_EP.search(p.name)
    title = _episode_title(re.sub(r"(\.(mkv|mp4|avi|m4v|ts))+$", "", p.name, flags=re.I), m.end())
    code = f"S{s:02d}E{e:0{pad}d}" + (f"-E{e2:0{pad}d}" if e2 else "")
    new = f"{show} - {code}" + (f" - {title}" if title else "") + p.suffix.lower()
    if new == p.name:
        return
    # Un nombre ya ordenado se deja aunque lleve el año de la carpeta o otro
    # relleno (S01E01 frente a S01E001): cambiarlo no aporta nada.
    prefix = "(?:" + re.escape(show) + "|" + re.escape(folder) + ")"
    if re.fullmatch(prefix + r" - S\d{2}E\d{2,4}(-E\d{2,4})?( - .+)?\.[a-z0-9]{2,4}", p.name) \
            and not TAGS.search(p.name[m.end():]) and "[" not in p.name:
        return
    reason = "extension repetida" if re.search(r"(\.(mkv|mp4|avi|m4v|ts)){2,}$", p.name, re.I) \
        else "limpiar nombre"
    _add_rename(plan, p, p.with_name(new), reason, group, taken)


def _fix_mixed_numbering(plan: Plan, show: str, group: str,
                         good_files: list[tuple[Path, int, int, Optional[int]]],
                         have: dict[int, set[int]], taken: set[str]) -> set[Path]:
    """Temporada que empieza en E01 pero tiene una cola con numeros absolutos
    (Fire Force S03E01-E13 y S03E71-E73): la cola se pasa a numeracion de la
    temporada restando los episodios de las temporadas anteriores. Devuelve
    los archivos que ya tienen propuesta."""
    fixed: set[Path] = set()
    for s in sorted(have):
        eps = sorted(have[s])
        if s < 2 or not eps or eps[0] != 1 or len(eps) < 2:
            continue
        gaps = [(eps[i + 1] - eps[i], i) for i in range(len(eps) - 1)]
        jump, idx = max(gaps)
        if jump <= 10:
            continue
        low, high = set(eps[:idx + 1]), eps[idx + 1:]
        offset = sum(max(have[t]) for t in have if 1 <= t < s and have[t] and min(have[t]) == 1)
        if not offset or high[0] - offset <= max(low) or any(e - offset in low for e in high):
            continue
        pad = 3 if max(eps) - offset >= 100 else 2
        for p, fs, e, e2 in good_files:
            if fs != s or e < high[0]:
                continue
            ne, ne2 = e - offset, (e2 - offset if e2 else None)
            code = f"S{s:02d}E{ne:0{pad}d}" + (f"-E{ne2:0{pad}d}" if ne2 else "")
            _add_rename(plan, p, p.with_name(f"{show} - {code}{p.suffix.lower()}"),
                        f"numero absoluto dentro de la temporada (E{e} = E{ne})", group, taken)
            fixed.add(p)
        have[s] = low | {e - offset for e in high}
    return fixed


def _ranges(nums: list[int]) -> str:
    """[1,2,3,7,9,10] -> 'E01-E03, E07, E09-E10'."""
    out, start, prev = [], None, None
    for n in nums + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(f"E{start:02d}" + (f"-E{prev:02d}" if prev != start else ""))
            start = prev = n
    return ", ".join(out)


# ---------------------------------------------------------------- peliculas

def movie_title(name: str) -> tuple[str, Optional[int]]:
    """'John.Wick.3.2019.1080p.WEB' -> ('John Wick 3', 2019)."""
    base = re.sub(r"\.(mkv|mp4|avi|m4v|ts)$", "", name, flags=re.I)
    s = re.sub(r"[._]+", " ", base)
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", s)
    year = None
    m = YEAR.search(s)
    if m and m.start() > 0:
        year = int(m.group(1))
        s = s[:m.start()]
    else:
        t = TAGS.search(s)
        if t:
            s = s[:t.start()]
    s = re.sub(r"[\s(\-]+$", "", re.sub(r"\s+", " ", s)).strip(" -(")
    return s, year


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    langs: list[str] = field(default_factory=list)
    duration_min: int = 0
    size: int = 0

    @property
    def quality(self) -> int:
        """0 SD, 1 720p, 2 1080p, 3 4K (por el ancho: 1920x800 sigue siendo 1080p)."""
        w = self.width
        return 3 if w >= 3800 else 2 if w >= 1900 else 1 if w >= 1260 else 0

    @property
    def dual(self) -> bool:
        return len({l for l in self.langs if l not in ("und", "")}) >= 2 or len(self.langs) >= 2

    def label(self) -> str:
        q = ["SD", "720p", "1080p", "4K"][self.quality]
        return f"{q} {self.width}x{self.height}, audio {'/'.join(self.langs) or '?'}, " \
               f"{self.duration_min} min, {self.size / 1e9:.1f} GB"


def probe_media(ffprobe: list[str], path: Path) -> MediaInfo:
    cmd = ffprobe + ["-v", "error", "-show_entries",
                     "stream=codec_type,width,height:stream_tags=language:format=duration",
                     "-of", "json", str(path)]
    info = MediaInfo(size=path.stat().st_size)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        return info
    data = json.loads(res.stdout or "{}")
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info.width:
            info.width, info.height = s.get("width") or 0, s.get("height") or 0
        elif s.get("codec_type") == "audio":
            info.langs.append(s.get("tags", {}).get("language", "und"))
    dur = data.get("format", {}).get("duration")
    info.duration_min = int(float(dur) / 60) if dur else 0
    return info


def pick_duplicate(a: MediaInfo, b: MediaInfo) -> tuple[Optional[int], str]:
    """Regla: si la mas pequeña es menor de 1080p y sin audio dual, se quita
    esa; si las dos tienen la misma calidad, se quita la mas grande.
    Devuelve el indice (0/1) a quitar o None si hay que decidir a mano."""
    small, big = (0, 1) if a.size <= b.size else (1, 0)
    infos = (a, b)
    if infos[small].quality < 2 and not infos[small].dual:
        return small, "la mas pequeña es inferior a 1080p y sin audio dual"
    if a.quality == b.quality and a.dual == b.dual:
        return big, "misma calidad: se quita la mas grande"
    return None, "calidades distintas: decidir a mano"


def _movie_files(root: Path) -> list[tuple[Path, Optional[Path]]]:
    """(video, carpeta de la pelicula o None si esta suelta en la raiz)."""
    out = []
    for f in sorted(root.iterdir()):
        if f.name.startswith((".", "#", "@")):
            continue
        if f.is_file() and is_video(f):
            out.append((f, None))
        elif f.is_dir():
            vids = [v for v in sorted(f.rglob("*")) if v.is_file() and is_video(v)
                    and not any(part.lower() in EXTRAS_DIRS for part in v.relative_to(f).parts[:-1])]
            for v in vids:
                out.append((v, f))
    return out


def plan_movies(root: Path, ffprobe: Optional[list[str]] = None) -> Plan:
    """Revisa una biblioteca de peliculas. Con ffprobe compara duplicados y da
    la duracion de las que no tienen año (ayuda a saber que version es)."""
    root = Path(root)
    plan = Plan()
    groups: dict[tuple[str, Optional[int]], list[Path]] = {}
    files = _movie_files(root)
    per_folder: dict[Path, int] = {}
    for _, folder in files:
        if folder:
            per_folder[folder] = per_folder.get(folder, 0) + 1
    for video, folder in files:
        # Una carpeta con varias peliculas (una coleccion): cada archivo es la suya.
        if folder and per_folder[folder] > 1:
            folder = None
        title, year = movie_title(folder.name if folder else video.name)
        if year is None and folder:
            title, year = movie_title(video.name)
        if year is None:
            data = {"title": title, "folder": str(folder) if folder else ""}
            if ffprobe:
                data["duration_min"] = probe_media(ffprobe, video).duration_min
            plan.issues.append(Issue(str(video), "pelicula sin año",
                                     "indica el año para renombrarla", "Películas sin año", data))
        groups.setdefault((_norm(title), year), []).append(video)

    for (title, year), vids in sorted(groups.items(), key=lambda kv: kv[0][0]):
        if len(vids) < 2 or year is None:
            continue
        group = f"Duplicada: {vids[0].name}"
        infos = [probe_media(ffprobe, v) for v in vids] if ffprobe else [MediaInfo(size=v.stat().st_size) for v in vids]
        data = {"files": [{"path": str(v), "info": i.label()} for v, i in zip(vids, infos)]}
        if len(vids) == 2 and ffprobe:
            idx, why = pick_duplicate(infos[0], infos[1])
            if idx is not None:
                plan.ops.append(Op(str(vids[idx]), "", "trash",
                                   f"{why}; se queda «{vids[1 - idx].name}»", group))
                continue
            plan.issues.append(Issue(str(vids[0]), "pelicula duplicada", why, group, data))
        else:
            plan.issues.append(Issue(str(vids[0]), "pelicula duplicada",
                                     "mas de dos copias o sin ffprobe: decidir a mano", group, data))
    return plan


def plan_movie_year(video: Path, year: int, root: Path) -> list[Op]:
    """Renombra una pelicula sin año: el archivo suelto, o su carpeta."""
    video, root = Path(video), Path(root)
    if video.parent == root:
        title, _ = movie_title(video.name)
        return [Op(str(video), str(video.with_name(f"{title} ({year}){video.suffix.lower()}")),
                   "rename", "año indicado a mano", "Películas sin año")]
    folder = video.parent
    while folder.parent != root:
        folder = folder.parent
    title, _ = movie_title(folder.name)
    return [Op(str(folder), str(folder.with_name(f"{title} ({year})")),
               "rename", "año indicado a mano", "Películas sin año")]


# ---------------------------------------------------------------- aplicar

def _owner_like_parent(path: Path) -> None:
    try:
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def apply_ops(ops: Iterable[Op], log_file: Path, trash_dir: Optional[Path]) -> list[dict]:
    """Ejecuta los movimientos (nunca borra) y los apunta en log_file (JSON por
    linea) para poder deshacer. 'trash' mueve a trash_dir conservando la ruta."""
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with open(log_file, "a", encoding="utf-8") as log:
        for op in ops:
            src = Path(op.src)
            if op.kind == "trash":
                if trash_dir is None:
                    results.append({"src": op.src, "status": "error", "detail": "sin papelera configurada"})
                    continue
                dst = Path(trash_dir) / str(src.resolve()).lstrip("/")
            else:
                dst = Path(op.dst)
            try:
                if not src.exists():
                    raise OSError("el origen ya no existe")
                if dst.exists():
                    raise OSError(f"ya existe {dst}")
                created = []
                d = dst.parent
                while not d.exists():
                    created.append(d)
                    d = d.parent
                for d in reversed(created):
                    d.mkdir()
                    _owner_like_parent(d)
                shutil.move(str(src), str(dst))
                log.write(json.dumps({"t": time.time(), "src": str(src), "dst": str(dst),
                                      "kind": op.kind, "dirs": [str(d) for d in created]}) + "\n")
                log.flush()
                results.append({"src": op.src, "dst": str(dst), "status": "ok"})
            except OSError as exc:
                results.append({"src": op.src, "status": "error", "detail": str(exc)})
    return results


def undo_log(log_file: Path) -> list[dict]:
    """Deshace un registro de apply_ops, del ultimo movimiento al primero."""
    entries = [json.loads(l) for l in Path(log_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    results = []
    for e in reversed(entries):
        src, dst = Path(e["src"]), Path(e["dst"])
        try:
            if src.exists():
                raise OSError(f"ya existe {src}")
            if not dst.exists():
                raise OSError(f"no existe {dst}")
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            for d in e.get("dirs", []):
                try:
                    Path(d).rmdir()
                except OSError:
                    pass
            results.append({"src": str(src), "status": "ok"})
        except OSError as exc:
            results.append({"src": str(src), "status": "error", "detail": str(exc)})
    Path(log_file).rename(str(log_file) + ".deshecho")
    return results
