# mkv-seekfix

Herramienta con interfaz web para dejar a punto una biblioteca de Jellyfin:

- **Nombres**: renombra episodios a `Serie - S01E01`, avisa de episodios que
  faltan, de películas sin año y de películas duplicadas.
- **Saltos MKV**: arregla los MKV que **no se pueden adelantar ni atrasar** en
  algunas TV (típicamente Android TV / ExoPlayer), sin recodificar.

Nada se borra: lo que se quita va a una papelera, los originales de MKV a un
respaldo, y cada cambio de nombre queda en un registro que se puede deshacer.

## Instalar con Docker

El servidor construye la imagen con el código de este repositorio (rama
`main`), sin registros intermedios. Solo necesita Docker y `curl` (no `git`).

1. Crea una carpeta, por ejemplo `/volume1/docker/mkv-seekfix`, y pon en ella
   [`docker-compose.yml`](docker-compose.yml) y
   [`scripts/actualizar.sh`](scripts/actualizar.sh).
2. En `docker-compose.yml` ajusta la ruta de tu biblioteca (`/media`) y, si
   quieres, una contraseña (`MKV_SEEKFIX_PASSWORD`).
3. Instala:

   ```
   sh actualizar.sh
   ```

Se abre en `http://IP-DEL-SERVIDOR:8765`.

### Actualizar

El mismo comando:

```
sh actualizar.sh
```

Descarga lo último de `main` en `src/`, reconstruye la imagen, reinicia el contenedor y
**borra la imagen anterior y la caché de construcción** que quedan sin uso, para
no acumular imágenes viejas. Los ajustes y registros se conservan en `/config`.

## Uso de la web

- **Carpetas**: elige con un navegador de carpetas qué revisar y si cada una es
  de *Series / Anime* o de *Películas*. Solo se puede navegar dentro de
  `/media`.
- **Nombres**: escanea, muestra los cambios agrupados por serie con casillas y
  los aplica con un botón.
  - Series y anime: cada episodio queda como `Serie - S01E01.ext` en su
    carpeta de temporada (crea `Season 01` si están sueltos). Entiende `1x01`,
    `S01.E01`, `Temporada 1 Capitulo 1`, `1.1`, números sueltos, episodios
    dobles (`026-027` → `S01E026-E027`) y numeración absoluta (temporada 2 que
    empieza en el 13). Los subtítulos con el mismo nombre acompañan al video.
  - **Episodios que faltan**: huecos dentro de cada temporada y, con clave de
    TMDB, los que faltan al final según TMDB.
  - **Películas sin año**: con clave de TMDB propone el año eligiendo el
    resultado cuya duración coincide con la del archivo (`Papillon` de
    150 min → 1973, no 2017). Sin clave, el año se escribe a mano.
  - **Películas duplicadas**: si la copia más pequeña es inferior a 1080p y sin
    audio dual, propone quitarla; si las dos tienen la misma calidad, propone
    quitar la más grande; si no, decides tú.
- **Saltos MKV**: escanear, simular, arreglar y restaurar los originales.
- **Registros**: cada aplicación de cambios guarda un registro; *Deshacer*
  devuelve cada archivo a su sitio.
- **Ajustes**: clave e idioma de TMDB, carpetas de papelera y de respaldo.

### Clave de TMDB (opcional)

Gratis en [themoviedb.org](https://www.themoviedb.org/settings/api) → Ajustes →
API. Vale la clave v3 o el token de lectura v4. Se guarda en
`/config/settings.json`.

## Saltos MKV: qué pasa y qué hace

Un MKV necesita un índice de saltos (*Cues*). Muchos reproductores solo lo
encuentran si el archivo lo referencia **al principio**. Si el índice está
solo en medio o al final y nada lo señala, la TV no puede saltar. FFmpeg sí lo
encuentra, por eso en el servidor el archivo "parece" estar bien.

El arreglo reempaqueta el archivo **sin recodificar**
(`-c copy -cues_to_front 1`): misma imagen, mismo sonido, mismos subtítulos y
capítulos. Antes de sustituir nada, verifica el resultado:

- mismos flujos (video, audios, subtítulos),
- misma duración (±0,5 s) cuando el original la declara,
- índice presente al principio,
- tamaño coherente (±2 %).

Si algo no cuadra, el original no se toca. Si todo cuadra, el original se
mueve a la carpeta de respaldo y el nuevo ocupa su lugar con los mismos
permisos y dueño.

## Línea de comandos (sin Docker)

Requisitos: Python 3.9 o superior (sin dependencias de pip), y `ffmpeg` y
`ffprobe` en el PATH.

```
pip install .

# Web
mkv-seekfix web --media-root /media --config-dir ./config --port 8765

# Nombres (solo lectura)
mkv-seekfix names /media/anime
mkv-seekfix names /media/peliculas --kind movies

# Saltos MKV
mkv-seekfix scan /ruta/biblioteca --list-file afectados.txt
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo --dry-run
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo --log fix.log
mkv-seekfix restore --backup-dir /ruta/respaldo
```

`fix` exige `--backup-dir` o `--no-backup` de forma explícita. Opciones útiles:

| Opción | Para qué |
|---|---|
| `--limit N` | procesar solo N archivos (para probar) |
| `--min-age S` | omitir archivos modificados hace menos de S s (descargas en curso; def. 300) |
| `--min-free-gb G` | parar si queda menos espacio libre (def. 50) |
| `--work-dir D` | carpeta temporal (def.: junto al archivo, para renombrar de forma atómica) |
| `--log F` | registro en archivo |

`scan` devuelve código 1 si hay afectados, y `fix` devuelve 1 si hubo errores.

Si `ffmpeg` solo existe en otro contenedor (por ejemplo el de Jellyfin):

```
mkv-seekfix fix /datos/media \
  --backup-dir /datos/respaldo \
  --ffmpeg  "docker exec -u 1000:1000 jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg" \
  --ffprobe "docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe" \
  --path-map /datos/media=/media
```

Variables de entorno: `MKV_SEEKFIX_MEDIA`, `MKV_SEEKFIX_CONFIG`,
`MKV_SEEKFIX_PORT`, `MKV_SEEKFIX_PASSWORD`, `MKV_SEEKFIX_FFMPEG`,
`MKV_SEEKFIX_FFPROBE`.

## Límites conocidos

- La detección de saltos es una heurística sobre los primeros 64 KB
  (`--probe-bytes`). Un archivo con adjuntos muy grandes antes del índice
  podría marcarse como afectado sin serlo; arreglarlo no hace daño.
- Reescribir un MKV usa disco y tarda (≈ velocidad de copia). Si el archivo se
  comparte por torrent, el torrent lo dará por dañado.
- Solo MKV. Los MP4 con el `moov` al final son otro caso.
- Nombres: si una carpeta de temporada no dice qué temporada es (por ejemplo
  arcos de One Piece) y el archivo no lleva `SxxEyy`, se avisa en vez de
  adivinar. La comparación con TMDB usa el primer resultado de búsqueda:
  revisa el nombre que muestra el aviso.

## Tests

```
python3 -m unittest discover -s tests -v
```

Los tests que usan ffmpeg se omiten si no está instalado.

## Aviso

La herramienta solo renombra, mueve y reempaqueta archivos que ya tienes; no
descarga ni comparte contenido.

Este producto usa la API de TMDB, pero no está respaldado ni certificado por
TMDB.
