# mkv-seekfix

[English](README.md) · **Español**

Deja a punto una biblioteca de Jellyfin desde una interfaz web:

- Renombra episodios a `Serie - S01E01` y avisa de episodios que faltan, películas sin año y duplicados.
- Arregla los MKV que no permiten adelantar ni atrasar en algunas TV, sin recodificar.

Lo que se quita va a una papelera, los MKV originales a un respaldo, y los cambios de nombre se pueden deshacer.

## Instalar

Requiere Docker y `curl`.

1. Pon [`docker-compose.yml`](docker-compose.yml) y [`scripts/actualizar.sh`](scripts/actualizar.sh) en una carpeta.
2. En `docker-compose.yml`, ajusta la ruta de tu biblioteca y `MKV_SEEKFIX_PASSWORD`.
3. Ejecuta `sh actualizar.sh` y abre `http://IP-DEL-SERVIDOR:8765`.

Para actualizar, ejecuta otra vez `sh actualizar.sh` o usa **Ajustes → Actualización** en la web.

La carpeta de respaldo debe ser exclusiva de esta herramienta. No puede ser ni contener la biblioteca, la papelera, la configuración ni el home.

## Línea de comandos

Requiere Python 3.9 o superior, `ffmpeg` y `ffprobe`.

```sh
pip install .

mkv-seekfix web --media-root /media --config-dir ./config
mkv-seekfix names /media/anime
mkv-seekfix scan /ruta/biblioteca --list-file afectados.txt
mkv-seekfix fix --from-list afectados.txt --backup-dir /ruta/respaldo
mkv-seekfix restore --backup-dir /ruta/respaldo
```

## Aviso

Este producto usa la API de TMDB, pero no está respaldado ni certificado por TMDB.
