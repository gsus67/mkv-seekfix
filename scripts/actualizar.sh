#!/bin/sh
# Instala o actualiza mkv-seekfix desde GitHub y borra lo que queda viejo.
# Se ejecuta en la carpeta donde esta docker-compose.yml.
set -e
cd "$(dirname "$0")"

docker compose build --pull
docker compose up -d

# La imagen anterior queda sin etiqueta al reconstruir: se borra, igual que la
# cache de construccion que ya no usa nada. No toca imagenes de otros contenedores.
docker image prune -f
docker builder prune -f

docker image ls mkv-seekfix
