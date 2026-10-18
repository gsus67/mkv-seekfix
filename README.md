# mkv-seekfix

Arregla los MKV que **no se pueden adelantar ni atrasar** en algunas TV
(típicamente Android TV / ExoPlayer): al saltar, el capítulo reinicia desde el
principio.

## Qué pasa

Un MKV necesita un índice de saltos (*Cues*). Muchos reproductores solo lo
encuentran si el archivo lo referencia **al principio**. Si el índice está
solo en medio o al final y nada lo señala, la TV no puede saltar. FFmpeg sí lo
encuentra, por eso en el servidor el archivo "parece" estar bien.

## Qué hace

Reempaqueta el archivo **sin recodificar** (`-c copy -cues_to_front 1`): misma
imagen, mismo sonido, mismos subtítulos y capítulos. Antes de sustituir nada,
verifica el resultado:

- mismos flujos (video, audios, subtítulos),
- misma duración (±0,5 s) cuando el original la declara,
- índice presente al principio,
- tamaño coherente (±2 %).

Si algo no cuadra, el original no se toca. Si todo cuadra, el original se
mueve a la carpeta de respaldo y el nuevo ocupa su lugar con los mismos
permisos y dueño.

## Requisitos

- Python 3.9 o superior (sin dependencias de pip).
- `ffmpeg` y `ffprobe` en el PATH (o indicados con `--ffmpeg` / `--ffprobe`).

## Instalación

```
pip install .
# o sin instalar:
python3 -m mkv_seekfix --help
```

## Uso

```
# 1. Ver cuáles están afectados (solo lectura)
mkv-seekfix scan /ruta/a/la/biblioteca --list-file afectados.txt

# 2. Simular
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo --dry-run

# 3. Arreglar guardando los originales
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo --log fix.log

# Deshacer: devuelve los originales a su sitio
mkv-seekfix restore --backup-dir /ruta/respaldo
```

`fix` exige `--backup-dir` o `--no-backup` de forma explícita.

Opciones útiles de `fix`:

| Opción | Para qué |
|---|---|
| `--limit N` | procesar solo N archivos (para probar) |
| `--min-age S` | omitir archivos modificados hace menos de S s (descargas en curso; def. 300) |
| `--min-free-gb G` | parar si queda menos espacio libre (def. 50) |
| `--work-dir D` | carpeta temporal (def.: junto al archivo, para renombrar de forma atómica) |
| `--log F` | registro en archivo |

El `scan` devuelve código 1 si hay afectados, y `fix` devuelve 1 si hubo errores.

### ffmpeg dentro de un contenedor

Si `ffmpeg` solo existe en un contenedor Docker:

```
mkv-seekfix fix /datos/media \
  --backup-dir /datos/respaldo \
  --ffmpeg  "docker exec -u 1000:1000 mi-contenedor /usr/lib/jellyfin-ffmpeg/ffmpeg" \
  --ffprobe "docker exec mi-contenedor /usr/lib/jellyfin-ffmpeg/ffprobe" \
  --path-map /datos/media=/media
```

Las rutas se escriben como las ve el anfitrión y `--path-map` las traduce.
Variables de entorno equivalentes: `MKV_SEEKFIX_FFMPEG`, `MKV_SEEKFIX_FFPROBE`.

## Límites conocidos

- La detección es una heurística sobre los primeros 64 KB (`--probe-bytes`). Un
  archivo con adjuntos muy grandes antes del índice podría marcarse como
  afectado sin serlo; arreglarlo no hace daño.
- Si el original no declara duración, solo se comparan los flujos.
- Si el archivo se comparte por torrent, cambiará su contenido y el torrent lo
  dará por dañado.
- Reescribe cada archivo completo: usa disco y tarda (≈ velocidad de copia).
- Solo MKV. Los MP4 con el `moov` al final son otro caso.

## Tests

```
python3 -m unittest discover -s tests -v
```

Los tests que usan ffmpeg se omiten si no está instalado.
