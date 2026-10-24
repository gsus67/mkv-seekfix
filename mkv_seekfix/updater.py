"""Boton de actualizar de la web.

El contenedor no puede actualizarse a si mismo (se reinicia a mitad), asi que
lanza por la API de Docker un contenedor docker:cli temporal que ejecuta
actualizar.sh en la carpeta del compose. Cuando termina, el contenedor nuevo
borra la imagen docker:cli para no dejar nada suelto.

Necesita el socket de Docker montado y MKV_SEEKFIX_HOST_DIR (la carpeta del
compose tal como la ve el servidor).
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

SOCK = "/var/run/docker.sock"
HELPER_IMAGE = "docker"
HELPER_TAG = "cli"
HELPER_NAME = "mkv-seekfix-update"
REPO = os.environ.get("MKV_SEEKFIX_REPO", "gsus67/mkv-seekfix")
REF = os.environ.get("MKV_SEEKFIX_REF", "main")
VERSION_FILE = Path(__file__).with_name("VERSION")


class UpdateError(Exception):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.sock_path)
        self.sock = s


def docker(method: str, path: str, body: Optional[dict] = None,
           timeout: float = 30.0) -> tuple[int, bytes]:
    conn = _UnixConnection(SOCK, timeout)
    try:
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        return res.status, res.read()
    except OSError as exc:
        raise UpdateError(f"no se pudo hablar con Docker: {exc}") from exc
    finally:
        conn.close()


def current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return ""


_latest_cache: dict = {"t": 0.0, "sha": "", "msg": "", "date": ""}


def latest_version(max_age: float = 600.0) -> dict:
    """Ultimo commit de GitHub (con cache para no gastar el limite de la API)."""
    if time.time() - _latest_cache["t"] < max_age and _latest_cache["sha"]:
        return dict(_latest_cache)
    url = f"https://api.github.com/repos/{REPO}/commits/{REF}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.load(res)
    except Exception as exc:
        raise UpdateError(f"no se pudo consultar GitHub: {exc}") from exc
    commit = data.get("commit", {})
    _latest_cache.update(t=time.time(), sha=data.get("sha", ""),
                         msg=commit.get("message", "").splitlines()[0] if commit.get("message") else "",
                         date=commit.get("committer", {}).get("date", ""))
    return dict(_latest_cache)


def host_dir() -> str:
    return os.environ.get("MKV_SEEKFIX_HOST_DIR", "")


def can_update() -> tuple[bool, str]:
    if not host_dir():
        return False, "falta MKV_SEEKFIX_HOST_DIR en el compose"
    if not Path(SOCK).exists():
        return False, "falta montar /var/run/docker.sock en el compose"
    return True, ""


def helper_running() -> bool:
    status, _ = docker("GET", f"/containers/{HELPER_NAME}/json", timeout=10)
    return status == 200


def start_update() -> None:
    ok, why = can_update()
    if not ok:
        raise UpdateError(why)
    if helper_running():
        raise UpdateError("ya hay una actualizacion en marcha")
    # Descargar la imagen del ayudante (la respuesta es un flujo de progreso).
    status, body = docker("POST", f"/images/create?fromImage={HELPER_IMAGE}&tag={HELPER_TAG}",
                          timeout=600)
    if status != 200 or b'"error"' in body:
        raise UpdateError(f"no se pudo descargar {HELPER_IMAGE}:{HELPER_TAG}: {body[-300:]!r}")
    hd = host_dir()
    cmd = ("sh actualizar.sh > config/update.log 2>&1; "
           "echo \"== FIN codigo $?\" >> config/update.log")
    spec = {
        "Image": f"{HELPER_IMAGE}:{HELPER_TAG}",
        "Cmd": ["sh", "-c", cmd],
        "WorkingDir": hd,
        "Env": [f"MKV_SEEKFIX_REPO={REPO}", f"MKV_SEEKFIX_REF={REF}"],
        "HostConfig": {"AutoRemove": True,
                       "Binds": [f"{SOCK}:{SOCK}", f"{hd}:{hd}"]},
    }
    status, body = docker("POST", f"/containers/create?name={HELPER_NAME}", spec)
    if status != 201:
        raise UpdateError(f"no se pudo crear el ayudante: {body[-300:]!r}")
    cid = json.loads(body)["Id"]
    status, body = docker("POST", f"/containers/{cid}/start")
    if status not in (204, 304):
        raise UpdateError(f"no se pudo arrancar el ayudante: {body[-300:]!r}")


def cleanup_helper_image_later(max_wait: float = 1800.0) -> None:
    """Al arrancar: espera a que el ayudante termine y borra su imagen."""
    if not can_update()[0]:
        return

    def run():
        deadline = time.time() + max_wait
        try:
            while helper_running() and time.time() < deadline:
                time.sleep(5)
            if not helper_running():
                docker("DELETE", f"/images/{HELPER_IMAGE}:{HELPER_TAG}", timeout=60)
        except UpdateError:
            pass
    threading.Thread(target=run, daemon=True).start()
