# mkv-seekfix

**English** · [Español](README.es.md)

Tidies up a Jellyfin library from a web interface:

- Renames episodes to `Show - S01E01` and flags missing episodes, movies without a year and duplicates.
- Fixes MKV files that can't seek on some TVs, without re-encoding.

Removed files go to a trash folder, original MKVs to a backup folder, and renames can be undone.

## Install

Requires Docker and `curl`.

1. Put [`docker-compose.yml`](docker-compose.yml) and [`scripts/actualizar.sh`](scripts/actualizar.sh) in a folder.
2. In `docker-compose.yml`, set your library path and `MKV_SEEKFIX_PASSWORD`.
3. Run `sh actualizar.sh` and open `http://SERVER-IP:8765`.

Run `sh actualizar.sh` again to update, or use **Ajustes → Actualización** in the web interface.

The backup folder must be dedicated to this tool. It can't be or contain the library, trash, configuration or home folder.

## Command line

Requires Python 3.9 or later, `ffmpeg` and `ffprobe`.

```sh
pip install .

mkv-seekfix web --media-root /media --config-dir ./config
mkv-seekfix names /media/anime
mkv-seekfix scan /path/to/library --list-file affected.txt
mkv-seekfix fix --from-list affected.txt --backup-dir /path/to/backup
mkv-seekfix restore --backup-dir /path/to/backup
```

## Notice

This product uses the TMDB API but is not endorsed or certified by TMDB.
