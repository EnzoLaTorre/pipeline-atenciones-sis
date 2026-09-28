#!/usr/bin/env bash
# Descarga los archivos del dataset "Atenciones realizadas a los asegurados" (SIS).
#
# Uso:
#   ./scripts/download_data.sh                # descarga todo (1.58 GB)
#   ./scripts/download_data.sh 2021_01_06     # solo lo que coincida con el patron
#
# Las URLs se leen del catalogo en vez de escribirse a mano porque el patron de
# los nombres cambio en 2024: hasta 2023 terminan en _0.zip, desde 2024 no.
set -euo pipefail

API="https://www.datosabiertos.gob.pe/api/3/action/package_show?id=datos-de-atenciones-realizadas-los-asegurados-sis"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
RAW_DIR="data/raw"
PATTERN="${1:-}"

mkdir -p "$RAW_DIR"

echo ">> Consultando el catalogo de datos abiertos del Estado Peruano..."
URLS="$(curl -sSL --compressed -H "User-Agent: $UA" "$API" | python -c '
import json, sys
# En Windows, Python escribe \r\n cuando su salida va a una pipe. El \r final
# se cuela en la URL y curl la rechaza como "malformed". Esta linea fuerza \n.
sys.stdout.reconfigure(newline="\n")
doc = json.load(sys.stdin)
for r in doc["result"][0]["resources"]:
    if "zip" in (r.get("format") or "").lower():
        print(r["url"])
')"

if [ -z "$URLS" ]; then
  echo "ERROR: no se obtuvo ninguna URL del catalogo." >&2
  exit 1
fi

echo ">> Archivos publicados: $(echo "$URLS" | wc -l)"
echo ">> Destino: $RAW_DIR"
echo ""

while IFS= read -r url; do
  [ -z "$url" ] && continue
  name="$(basename "$url")"

  if [ -n "$PATTERN" ] && [[ "$name" != *"$PATTERN"* ]]; then
    continue
  fi

  if [ -f "$RAW_DIR/$name" ]; then
    echo "   ya existe, se omite: $name"
    continue
  fi

  echo "   descargando: $name"
  # -f hace que curl falle ante errores HTTP en vez de guardar la pagina de
  # error como si fuera un ZIP. Se descarga a .part y solo se renombra al
  # terminar bien, para que una descarga interrumpida no se confunda con una
  # completa en la siguiente corrida.
  if curl -sSfL --compressed -H "User-Agent: $UA" -o "$RAW_DIR/$name.part" "$url"; then
    mv "$RAW_DIR/$name.part" "$RAW_DIR/$name"
  else
    echo "   ERROR al descargar $name" >&2
    rm -f "$RAW_DIR/$name.part"
    exit 1
  fi
done <<< "$URLS"

echo ""
echo ">> Contenido de $RAW_DIR:"
ls -lh "$RAW_DIR"