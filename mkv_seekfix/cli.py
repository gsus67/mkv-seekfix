"""Linea de comandos: scan, fix y restore."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from . import core

ENV_FFMPEG = "MKV_SEEKFIX_FFMPEG"
ENV_FFPROBE = "MKV_SEEKFIX_FFPROBE"


def _logger(log_file):
    def log(msg: str) -> None:
        line = time.strftime("%F %T ") + msg
        print(line, flush=True)
        if log_file:
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    return log


def _collect(args) -> list[Path]:
    files: list[Path] = []
    if getattr(args, "from_list", None):
        with open(args.from_list, encoding="utf-8") as fh:
            files += [Path(line.rstrip("\n")) for line in fh if line.strip()]
    files += list(core.find_mkv(Path(p) for p in args.paths))
    seen, uniq = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def cmd_scan(args) -> int:
    bad = ok = 0
    out = open(args.list_file, "w", encoding="utf-8") if args.list_file else None
    try:
        for f in _collect(args):
            try:
                good = core.has_front_cues(f, args.probe_bytes)
            except OSError as exc:
                print(f"ERROR {f}: {exc}", file=sys.stderr)
                continue
            if good:
                ok += 1
            else:
                bad += 1
                print(f)
                if out:
                    out.write(str(f) + "\n")
    finally:
        if out:
            out.close()
    print(f"\nCon indice al frente: {ok} | Afectados: {bad}", file=sys.stderr)
    return 1 if bad else 0


def cmd_fix(args) -> int:
    log = _logger(args.log)
    if not args.no_backup and not args.backup_dir:
        print("Indique --backup-dir o --no-backup explicitamente.", file=sys.stderr)
        return 2
    tools = core.Tools.from_strings(args.ffmpeg, args.ffprobe, args.path_map)
    files = _collect(args)
    if args.limit:
        files = files[: args.limit]
    backup_dir = None if args.no_backup else Path(args.backup_dir)
    free_probe = backup_dir or Path(".")
    counts = {"ok": 0, "skip": 0, "error": 0, "dry-run": 0}
    for i, f in enumerate(files, 1):
        probe_path = free_probe if free_probe.exists() else f.parent
        if args.min_free_gb and shutil.disk_usage(probe_path).free < args.min_free_gb * 1e9:
            log(f"PARADO: queda menos de {args.min_free_gb} GB libres")
            break
        res = core.fix_file(tools, f, backup_dir, dry_run=args.dry_run, min_age=args.min_age,
                            work_dir=Path(args.work_dir) if args.work_dir else None,
                            probe_bytes=args.probe_bytes)
        counts[res.status] += 1
        log(f"[{i}/{len(files)}] {f} | {res.status.upper()} {res.detail}")
    log("FIN " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 1 if counts["error"] else 0


def cmd_restore(args) -> int:
    log = _logger(args.log)
    results = core.restore(Path(args.backup_dir), dry_run=args.dry_run)
    for r in results:
        log(f"{r.path} | {r.status.upper()} {r.detail}")
    return 1 if any(r.status == "error" for r in results) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mkv-seekfix", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_paths=True):
        if with_paths:
            sp.add_argument("paths", nargs="*", help="archivos o carpetas a revisar")
            sp.add_argument("--from-list", help="archivo con una ruta por linea")
        sp.add_argument("--probe-bytes", type=int, default=core.DEFAULT_PROBE_BYTES,
                        help="bytes iniciales donde buscar el indice (def. %(default)s)")

    s = sub.add_parser("scan", help="listar los MKV sin indice al frente (solo lectura)")
    common(s)
    s.add_argument("--list-file", help="guardar aqui la lista de afectados")
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fix", help="reempaquetar los afectados (sin recodificar)")
    common(f)
    f.add_argument("--backup-dir", help="carpeta donde guardar los originales")
    f.add_argument("--no-backup", action="store_true", help="NO guardar originales (arriesgado)")
    f.add_argument("--dry-run", action="store_true", help="solo mostrar que se haria")
    f.add_argument("--limit", type=int, help="procesar como maximo N archivos")
    f.add_argument("--min-age", type=float, default=300.0,
                   help="omitir archivos modificados hace menos de N segundos (def. %(default)s)")
    f.add_argument("--min-free-gb", type=float, default=50.0,
                   help="parar si queda menos espacio libre (def. %(default)s)")
    f.add_argument("--work-dir", help="carpeta temporal (def.: junto al archivo)")
    f.add_argument("--ffmpeg", default=os.environ.get(ENV_FFMPEG, "ffmpeg"),
                   help=f"comando de ffmpeg; env {ENV_FFMPEG}")
    f.add_argument("--ffprobe", default=os.environ.get(ENV_FFPROBE, "ffprobe"),
                   help=f"comando de ffprobe; env {ENV_FFPROBE}")
    f.add_argument("--path-map", action="append", default=[], metavar="HOST=DESTINO",
                   help="traducir rutas si ffmpeg corre en un contenedor (repetible)")
    f.add_argument("--log", help="archivo de registro")
    f.set_defaults(func=cmd_fix)

    r = sub.add_parser("restore", help="devolver los originales guardados")
    r.add_argument("--backup-dir", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--log", help="archivo de registro")
    r.set_defaults(func=cmd_restore)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in ("scan", "fix") and not args.paths and not args.from_list:
        print("Indique al menos una ruta o --from-list.", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
