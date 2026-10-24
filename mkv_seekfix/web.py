"""Interfaz web: elegir carpetas, revisar nombres, arreglar saltos de MKV.

Servidor de la biblioteca estandar (sin dependencias). Las tareas largas corren
de una en una en segundo plano y la pagina consulta su progreso. Todas las
rutas que llegan del navegador se limitan a MEDIA_ROOT.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from . import core, names, tmdb, updater

UI_FILE = Path(__file__).with_name("web_ui.html")


class Config:
    def __init__(self, media_root: Path, config_dir: Path, ffmpeg: str, ffprobe: str,
                 password: str = ""):
        self.media_root = Path(media_root).resolve()
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.config_dir / "logs"
        self.tools = core.Tools.from_strings(ffmpeg, ffprobe)
        self.password = password
        self.settings_file = self.config_dir / "settings.json"
        self.settings = {"tmdb_key": "", "tmdb_language": "es-ES",
                         "trash_dir": str(self.config_dir / "papelera"),
                         "backup_dir": str(self.config_dir / "respaldo-mkv"),
                         "folders": []}
        if self.settings_file.exists():
            self.settings.update(json.loads(self.settings_file.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.settings_file.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    def inside(self, path: str) -> Path:
        """Ruta resuelta dentro de MEDIA_ROOT, o ValueError."""
        p = Path(path).resolve()
        if p != self.media_root and self.media_root not in p.parents:
            raise ValueError(f"fuera de la carpeta permitida: {path}")
        return p


class Job:
    """Una tarea en segundo plano con progreso y registro de lineas."""

    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.running = True
        self.done = 0
        self.total = 0
        self.lines: list[str] = []
        self.result = None
        self.error = ""
        self.cancel = False
        self.started = time.time()

    def log(self, msg: str) -> None:
        self.lines.append(time.strftime("%H:%M:%S ") + msg)
        del self.lines[:-500]

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "running": self.running, "done": self.done,
                "total": self.total, "lines": self.lines[-200:], "result": self.result,
                "error": self.error}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.job: Optional[Job] = None
        self.lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Job], object]) -> Job:
        with self.lock:
            if self.job and self.job.running:
                raise RuntimeError("ya hay una tarea en marcha")
            job = self.job = Job(kind)

        def run():
            try:
                job.result = fn(job)
            except Exception as exc:
                job.error = f"{exc}"
                job.log("ERROR " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            finally:
                job.running = False
        threading.Thread(target=run, daemon=True).start()
        return job

    # ------------------------------------------------------------ nombres

    def names_scan(self, folders: list[dict]) -> Job:
        checked = [(self.cfg.inside(f["path"]), f["kind"]) for f in folders]
        ffprobe = self.cfg.tools.ffprobe

        def fn(job: Job):
            job.total = len(checked)
            out = {"ops": [], "issues": []}
            key = self.cfg.settings.get("tmdb_key", "")
            client = tmdb.Tmdb(key, self.cfg.settings.get("tmdb_language", "es-ES")) if key else None
            for root, kind in checked:
                job.log(f"Revisando {root} ({kind})")
                plan = names.plan_movies(root, ffprobe) if kind == "movies" else names.plan_series(root)
                if client and plan.shows:
                    job.log(f"  Comparando {len(plan.shows)} series con TMDB")
                    for show in plan.shows:
                        plan.issues += self._tmdb_missing(client, show)
                d = plan.to_dict()
                for item in d["ops"] + d["issues"]:
                    item["root"] = str(root)
                out["ops"] += d["ops"]
                out["issues"] += d["issues"]
                job.done += 1
                job.log(f"  {len(plan.ops)} cambios propuestos, {len(plan.issues)} avisos")
            for i, op in enumerate(out["ops"]):
                op["id"] = i
            return out
        return self.start("names-scan", fn)

    @staticmethod
    def _tmdb_missing(client: "tmdb.Tmdb", show: dict) -> list[names.Issue]:
        """Episodios que faltan al final de cada temporada segun TMDB."""
        m = names.YEAR.search(show["folder"])
        try:
            info = client.tv_seasons(show["show"], int(m.group(1)) if m else None)
        except tmdb.TmdbError:
            return []
        if not info:
            return []
        src = f"según TMDB «{info['name']}» ({info['year'] or '?'})"
        out = []
        for s, local in sorted(show["seasons"].items()):
            total = info["seasons"].get(int(s))
            if s == 0 or not total or local["last"] >= total:
                continue
            missing = list(range(local["last"] + 1, total + 1))
            out.append(names.Issue(show["path"], "faltan episodios al final",
                                   f"temporada {s}: faltan {names._ranges(missing)}; {src} tiene {total}",
                                   show["folder"], {"season": s, "missing": missing, "tmdb": info["name"]}))
        return out

    def names_apply(self, ops: list[dict]) -> Job:
        checked = []
        for o in ops:
            src = self.cfg.inside(o["src"])
            dst = self.cfg.inside(o["dst"]) if o.get("kind", "rename") == "rename" else None
            checked.append(names.Op(str(src), str(dst) if dst else "", o.get("kind", "rename"),
                                    o.get("reason", ""), o.get("group", "")))
        log_file = self.cfg.logs_dir / f"nombres-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        trash = Path(self.cfg.settings["trash_dir"]) if self.cfg.settings.get("trash_dir") else None

        def fn(job: Job):
            job.total = len(checked)
            results = []
            for op in checked:
                res = names.apply_ops([op], log_file, trash)[0]
                results.append(res)
                job.done += 1
                job.log(f"{res['status'].upper()} {Path(op.src).name}"
                        + (f" -> {Path(res['dst']).name}" if res.get("dst") else "")
                        + (f" ({res['detail']})" if res.get("detail") else ""))
            return {"results": results, "log": log_file.name}
        return self.start("names-apply", fn)

    def names_undo(self, log_name: str) -> Job:
        log_file = (self.cfg.logs_dir / Path(log_name).name)
        if not log_file.is_file():
            raise ValueError("registro no encontrado")

        def fn(job: Job):
            res = names.undo_log(log_file)
            for r in res:
                job.log(f"{r['status'].upper()} {r['src']} {r.get('detail', '')}")
            return {"results": res}
        return self.start("names-undo", fn)

    def logs(self) -> list[dict]:
        if not self.cfg.logs_dir.exists():
            return []
        out = []
        for f in sorted(self.cfg.logs_dir.iterdir(), reverse=True):
            if f.suffix == ".jsonl":
                n = sum(1 for _ in f.open(encoding="utf-8"))
                out.append({"name": f.name, "entries": n})
        return out

    def movie_year(self, path: str, root: str, year: int) -> list[dict]:
        ops = names.plan_movie_year(self.cfg.inside(path), int(year), self.cfg.inside(root))
        for o in ops:
            self.cfg.inside(o.dst)
        return [dict(asdict(o), root=root) for o in ops]

    def tmdb_lookup(self, title: str, duration_min: int) -> dict:
        key = self.cfg.settings.get("tmdb_key", "")
        if not key:
            raise ValueError("falta la clave de TMDB en Ajustes")
        client = tmdb.Tmdb(key, self.cfg.settings.get("tmdb_language", "es-ES"))
        cands = client.search_movie(title)
        best, why = tmdb.best_match(cands, int(duration_min or 0))
        return {"candidates": [dict(asdict(c), label=c.label()) for c in cands],
                "best": asdict(best) if best else None, "why": why}

    # ------------------------------------------------------------ saltos MKV

    def seek_scan(self, folders: list[str]) -> Job:
        roots = [self.cfg.inside(f) for f in folders]

        def fn(job: Job):
            files = list(core.find_mkv(roots))
            job.total = len(files)
            bad = []
            for f in files:
                if job.cancel:
                    break
                try:
                    if not core.has_front_cues(f):
                        bad.append(str(f))
                except OSError as exc:
                    job.log(f"ERROR {f}: {exc}")
                job.done += 1
            job.log(f"Revisados {job.done}: {len(bad)} sin indice al frente")
            return {"affected": bad, "checked": job.done}
        return self.start("seek-scan", fn)

    def seek_fix(self, files: list[str], dry_run: bool, min_free_gb: float = 50.0) -> Job:
        paths = [self.cfg.inside(f) for f in files]
        backup = Path(self.cfg.settings["backup_dir"])

        def fn(job: Job):
            job.total = len(paths)
            counts = {"ok": 0, "skip": 0, "error": 0, "dry-run": 0}
            for f in paths:
                if job.cancel:
                    job.log("Cancelado")
                    break
                probe = backup if backup.exists() else f.parent
                if not dry_run and min_free_gb and shutil.disk_usage(probe).free < min_free_gb * 1e9:
                    job.log(f"PARADO: queda menos de {min_free_gb} GB libres")
                    break
                res = core.fix_file(self.cfg.tools, f, backup, dry_run=dry_run)
                counts[res.status] += 1
                job.done += 1
                job.log(f"{res.status.upper()} {f.name} {res.detail}")
            return counts
        return self.start("seek-fix", fn)

    def seek_restore(self) -> Job:
        backup = Path(self.cfg.settings["backup_dir"])

        def fn(job: Job):
            res = core.restore(backup)
            for r in res:
                job.log(f"{r.status.upper()} {r.path} {r.detail}")
            return {"restored": sum(r.status == "ok" for r in res)}
        return self.start("seek-restore", fn)

    # ------------------------------------------------------------ actualizar

    def version(self, refresh: bool = False) -> dict:
        cur = updater.current_version()
        ok, why = updater.can_update()
        out = {"current": cur[:7], "can_update": ok, "why": why, "latest": "", "message": "",
               "update_available": False, "error": ""}
        try:
            latest = updater.latest_version(0 if refresh else 600)
            out.update(latest=latest["sha"][:7], message=latest["msg"], date=latest["date"],
                       update_available=bool(cur) and latest["sha"] != cur)
        except updater.UpdateError as exc:
            out["error"] = str(exc)
        return out

    def update_log(self) -> dict:
        log = self.cfg.config_dir / "update.log"
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        except OSError:
            lines = []
        running = False
        try:
            running = updater.can_update()[0] and updater.helper_running()
        except updater.UpdateError:
            pass
        return {"lines": lines, "running": running}

    # ------------------------------------------------------------ carpetas

    def browse(self, path: str) -> dict:
        p = self.cfg.inside(path or str(self.cfg.media_root))
        dirs = sorted((d.name for d in p.iterdir()
                       if d.is_dir() and not d.name.startswith((".", "@", "#"))), key=str.lower)
        parent = str(p.parent) if p != self.cfg.media_root else None
        return {"path": str(p), "parent": parent, "dirs": dirs}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mkv-seekfix"

        def log_message(self, fmt, *args):  # sin ruido en consola por cada consulta
            pass

        def _auth(self) -> bool:
            pw = app.cfg.password
            if not pw:
                return True
            hdr = self.headers.get("Authorization", "")
            if hdr.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(hdr[6:]).decode().partition(":")
                    if hmac.compare_digest(given, pw):
                        return True
                except Exception:
                    pass
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="mkv-seekfix"')
            self.end_headers()
            return False

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            if not self._auth():
                return
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/state":
                    s = dict(app.cfg.settings)
                    s["tmdb_key"] = "********" if s.get("tmdb_key") else ""
                    self._json({"settings": s, "media_root": str(app.cfg.media_root),
                                "job": app.job.to_dict() if app.job else None})
                elif url.path == "/api/job":
                    self._json(app.job.to_dict() if app.job else None)
                elif url.path == "/api/browse":
                    self._json(app.browse(q.get("path", "")))
                elif url.path == "/api/logs":
                    self._json(app.logs())
                elif url.path == "/api/version":
                    self._json(app.version(q.get("refresh") == "1"))
                elif url.path == "/api/update/log":
                    self._json(app.update_log())
                else:
                    self._json({"error": "no existe"}, 404)
            except (ValueError, OSError) as exc:
                self._json({"error": str(exc)}, 400)

        def do_POST(self):
            if not self._auth():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/settings":
                    for k in ("tmdb_language", "trash_dir", "backup_dir", "folders"):
                        if k in body:
                            app.cfg.settings[k] = body[k]
                    if body.get("tmdb_key") and body["tmdb_key"] != "********":
                        app.cfg.settings["tmdb_key"] = body["tmdb_key"]
                    app.cfg.save()
                    self._json({"ok": True})
                elif path == "/api/names/scan":
                    self._json(app.names_scan(body["folders"]).to_dict())
                elif path == "/api/names/apply":
                    self._json(app.names_apply(body["ops"]).to_dict())
                elif path == "/api/names/undo":
                    self._json(app.names_undo(body["log"]).to_dict())
                elif path == "/api/names/year":
                    self._json({"ops": app.movie_year(body["path"], body["root"], body["year"])})
                elif path == "/api/names/tmdb":
                    self._json(app.tmdb_lookup(body["title"], body.get("duration_min", 0)))
                elif path == "/api/seek/scan":
                    self._json(app.seek_scan(body["folders"]).to_dict())
                elif path == "/api/seek/fix":
                    self._json(app.seek_fix(body["files"], bool(body.get("dry_run")),
                                            float(body.get("min_free_gb", 50))).to_dict())
                elif path == "/api/seek/restore":
                    self._json(app.seek_restore().to_dict())
                elif path == "/api/update":
                    if app.job and app.job.running:
                        raise RuntimeError("espera a que termine la tarea en marcha")
                    updater.start_update()
                    self._json({"ok": True})
                elif path == "/api/job/cancel":
                    if app.job:
                        app.job.cancel = True
                    self._json({"ok": True})
                else:
                    self._json({"error": "no existe"}, 404)
            except (ValueError, KeyError, RuntimeError, OSError, tmdb.TmdbError,
                    updater.UpdateError) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler


def serve(media_root: str, config_dir: str, host: str = "0.0.0.0", port: int = 8765,
          ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", password: str = "") -> None:
    cfg = Config(Path(media_root), Path(config_dir), ffmpeg, ffprobe, password)
    httpd = ThreadingHTTPServer((host, port), make_handler(App(cfg)))
    updater.cleanup_helper_image_later()
    print(f"mkv-seekfix web en http://{host}:{port}  (media: {cfg.media_root})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
