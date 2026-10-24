#!/bin/sh
# Instala o actualiza mkv-seekfix desde GitHub y borra lo que queda viejo.
# Se ejecuta en la carpeta donde esta docker-compose.yml. Necesita curl o wget
# (no hace falta git en el servidor). El boton "Actualizar" de la web lo lanza
# dentro de un contenedor docker:cli temporal.
set -e
cd "$(dirname "$0")"

REPO="${MKV_SEEKFIX_REPO:-gsus67/mkv-seekfix}"
REF="${MKV_SEEKFIX_REF:-main}"

fetch() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"; else wget -qO- "$1"; fi
}

echo "== Buscando la ultima version de $REPO ($REF)"
SHA=$(fetch "https://api.github.com/repos/$REPO/commits/$REF" | sed -n 's/^  "sha": "\([0-9a-f]*\)",$/\1/p' | head -1)
[ -n "$SHA" ] || { echo "No se pudo leer la version de GitHub"; exit 1; }
echo "   commit ${SHA%"${SHA#???????}"}"

echo "== Descargando el codigo"
rm -rf src.new
mkdir src.new
fetch "https://github.com/$REPO/archive/$SHA.tar.gz" | tar xz -C src.new --strip-components=1
printf '%s\n' "$SHA" > src.new/mkv_seekfix/VERSION
rm -rf src
mv src.new src

echo "== Construyendo la imagen"
docker compose build --pull
echo "== Reiniciando el contenedor"
docker compose up -d

# La imagen anterior queda sin etiqueta al reconstruir: se borra, igual que la
# cache de construccion que ya no usa nada. No toca imagenes de otros contenedores.
echo "== Borrando imagenes y cache viejas"
docker image prune -f
docker builder prune -f

docker image ls mkv-seekfix
echo "== Listo"
