# mkv-seekfix

**English** · [Español](README.es.md)

A tool with a web interface for tidying up a Jellyfin library:

- **Names:** renames episodes to `Show - S01E01`, and flags missing episodes,
  movies without a year and duplicate movies.
- **MKV seeking:** fixes MKV files that **can't be fast-forwarded or rewound** on
  some TVs (typically Android TV / ExoPlayer), without re-encoding.

Removed files go to a trash folder, original MKVs go to a backup folder, and
every rename is logged so it can be undone.

## Install with Docker

The server builds the image from this repository's `main` branch. It only needs
Docker and `curl`.

1. Create a folder such as `/volume1/docker/mkv-seekfix` and put
   [`docker-compose.yml`](docker-compose.yml) and
   [`scripts/actualizar.sh`](scripts/actualizar.sh) in it.
2. In `docker-compose.yml`, set your library path (`/media`) and a password
   (`MKV_SEEKFIX_PASSWORD`).
3. Run `sh actualizar.sh`.

It opens at `http://SERVER-IP:8765`.

### Update

Use **Ajustes → Actualización → Actualizar ahora** in the web interface, or run
`sh actualizar.sh` in the compose folder. Settings and logs stay in `/config`.

Updating from the web interface needs the Docker socket and
`MKV_SEEKFIX_HOST_DIR` from the example compose file. With the socket mounted,
the web interface controls the server, so **set a password**.

## Using the web interface

- **Folders:** choose what to check inside `/media`, and mark each folder as
  series/anime or movies.
- **Scan:** shows one card per show with its seasons, missing episodes, proposed
  renames, warnings and MKVs without a seek index. Apply one show or everything
  selected.
- **Logs:** undo any applied set of changes. Moves that can't be undone stay in
  the log so you can retry.
- **Settings:** updates, MKV backup, TMDB key, and trash and backup folders.

The backup folder must be dedicated to this tool: it can't be or contain the
library, trash, configuration or home folder. **Empty** only works on the default
folder or one that was empty when first used.

For the end of each season and movie years, add a free
[TMDB API key](https://www.themoviedb.org/settings/api). It's stored in
`/config/settings.json`.

## How the MKV fix works

Many players only find an MKV's seek index (*Cues*) when it's referenced at the
start of the file. The fix remuxes the file with `-c copy -cues_to_front 1`, then
checks that the streams, duration, index and size are right. If a check fails,
the original isn't touched. If all pass, the original moves to the backup folder.

## Command line

Requires Python 3.9 or later, plus `ffmpeg` and `ffprobe` in the `PATH`.

```sh
pip install .

mkv-seekfix web --media-root /media --config-dir ./config --port 8765
mkv-seekfix names /media/anime
mkv-seekfix names /media/movies --kind movies
mkv-seekfix scan /path/to/library --list-file affected.txt
mkv-seekfix fix --from-list affected.txt --backup-dir /path/to/backup --dry-run
mkv-seekfix restore --backup-dir /path/to/backup
```

`names` is read-only. `fix` requires `--backup-dir` or `--no-backup`. Run
`mkv-seekfix fix --help` for all options, including running `ffmpeg` from another
container.

## Known limits

- Detection checks the first 64 KB, so a file with very large attachments may be
  flagged by mistake. Fixing it does no harm.
- Remuxing uses disk space and takes about as long as copying the file. A torrent
  client will report a rewritten file as damaged.
- MKV only.
- If a season folder doesn't say which season it is and the file has no `SxxEyy`,
  the tool warns you instead of guessing.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests that use ffmpeg are skipped when it isn't installed.

## Notice

This tool only renames, moves and remuxes files you already have. It doesn't
download or share content.

This product uses the TMDB API but is not endorsed or certified by TMDB.
