# mkv-seekfix

[English](README.md) · **Español**

Herramienta con interfaz web para dejar a punto una biblioteca de Jellyfin:

- **Nombres:** renombra episodios a `Serie - S01E01` y avisa de episodios que
  faltan, películas sin año y películas duplicadas.
- **Saltos MKV:** arregla los MKV que **no se pueden adelantar ni atrasar** en
  algunas TV (típicamente Android TV / ExoPlayer), sin recodificar.

Lo que se quita va a una papelera, los MKV originales van a un respaldo y cada
cambio de nombre queda en un registro que se puede deshacer.

## Instalar con Docker

El servidor construye la imagen desde la rama `main` de este repositorio. Solo
necesita Docker y `curl`.

1. Crea una carpeta, por ejemplo `/volume1/docker/mkv-seekfix`, y pon en ella
   [`docker-compose.yml`](docker-compose.yml) y
   [`scripts/actualizar.sh`](scripts/actualizar.sh).
2. En `docker-compose.yml`, ajusta la ruta de tu biblioteca (`/media`) y una
   contraseña (`MKV_SEEKFIX_PASSWORD`).
3. Ejecuta `sh actualizar.sh`.

Se abre en `http://IP-DEL-SERVIDOR:8765`.

### Actualizar

Usa **Ajustes → Actualización → Actualizar ahora** en la web, o ejecuta
`sh actualizar.sh` en la carpeta del compose. Los ajustes y registros se conservan
en `/config`.

Actualizar desde la web necesita el socket de Docker y `MKV_SEEKFIX_HOST_DIR` del
compose de ejemplo. Con el socket montado, la web controla el servidor, así que
**pon una contraseña**.

## Uso de la web

- **Carpetas:** elige qué revisar dentro de `/media` y marca cada carpeta como
  series/anime o películas.
- **Escaneo:** muestra una ficha por serie con sus temporadas, episodios que
  faltan, cambios de nombre propuestos, avisos y MKV sin índice de saltos. Aplica
  una serie o todo lo seleccionado.
- **Registros:** deshace cualquier grupo de cambios aplicado. Los movimientos que
  no se pueden deshacer siguen en el registro para reintentarlos.
- **Ajustes:** actualización, respaldo de MKV, clave de TMDB y carpetas de papelera
  y respaldo.

La carpeta de respaldo debe ser exclusiva de esta herramienta: no puede ser ni
contener la biblioteca, la papelera, la configuración ni el home. **Vaciar** solo
actúa sobre la carpeta por defecto o una que estaba vacía cuando se usó por primera
vez.

Para el final de cada temporada y el año de las películas, añade una
[clave gratuita de la API de TMDB](https://www.themoviedb.org/settings/api). Se
guarda en `/config/settings.json`.

## Cómo funciona el arreglo de MKV

Muchos reproductores solo encuentran el índice de saltos (*Cues*) de un MKV si está
referenciado al principio del archivo. El arreglo reempaqueta el archivo con
`-c copy -cues_to_front 1` y comprueba que los flujos, la duración, el índice y el
tamaño sean correctos. Si algo falla, el original no se toca. Si todo está bien, el
original pasa a la carpeta de respaldo.

## Línea de comandos

Requiere Python 3.9 o superior, además de `ffmpeg` y `ffprobe` en el `PATH`.

```sh
pip install .

mkv-seekfix web --media-root /media --config-dir ./config --port 8765
mkv-seekfix names /media/anime
mkv-seekfix names /media/peliculas --kind movies
mkv-seekfix scan /ruta/biblioteca --list-file afectados.txt
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo --dry-run
mkv-seekfix restore --backup-dir /ruta/respaldo
```

`names` es de solo lectura. `fix` exige `--backup-dir` o `--no-backup`. Ejecuta
`mkv-seekfix fix --help` para ver todas las opciones, incluida la de usar `ffmpeg`
desde otro contenedor.

## Límites conocidos

- La detección revisa los primeros 64 KB, así que un archivo con adjuntos muy
  grandes puede marcarse por error. Arreglarlo no hace daño.
- Reempaquetar usa espacio en disco y tarda lo mismo que copiar el archivo,
  aproximadamente. Un cliente de torrent dará por dañado un archivo reescrito.
- Solo MKV.
- Si una carpeta de temporada no dice qué temporada es y el archivo no lleva
  `SxxEyy`, la herramienta avisa en vez de adivinar.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Los tests que usan ffmpeg se omiten si no está instalado.

## Aviso

Esta herramienta solo renombra, mueve y reempaqueta archivos que ya tienes. No
descarga ni comparte contenido.

Este producto usa la API de TMDB, pero no está respaldado ni certificado por TMDB.
