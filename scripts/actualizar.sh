#!/bin/sh
# Instala o actualiza mkv-seekfix desde GitHub y borra lo que queda viejo.
# Se ejecuta en la carpeta donde esta docker-compose.yml. Solo necesita curl
# (no hace falta git en el servidor).
set -e
cd "$(dirname "$0")"

REPO="${MKV_SEEKFIX_REPO:-gsus67/mkv-seekfix}"
REF="${MKV_SEEKFIX_REF:-main}"

rm -rf src.new
mkdir src.new
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$REF.tar.gz" | tar xz -C src.new --strip-components=1
rm -rf src
mv src.new src

docker compose build --pull
docker compose up -d

# La imagen anterior queda sin etiqueta al reconstruir: se borra, igual que la
# cache de construccion que ya no usa nada. No toca imagenes de otros contenedores.
docker image prune -f
docker builder prune -f

docker image ls mkv-seekfix
