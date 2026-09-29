#!/usr/bin/env bash
# Descarga los dos datasets de contexto que usa el pipeline para el denominador
# de las tasas y para validar los codigos de distrito:
#
#   - RIDA: poblacion identificada con DNI (RENIEC), a nivel de persona
#   - Ubigeos: catalogo oficial de distritos del INEI
#
# Las URLs salen del catalogo CKAN, no estan escritas a mano.
set -euo pipefail

API="https://www.datosabiertos.gob.pe/api/3/action"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
RAW_DIR="data/raw"

mkdir -p "$RAW_DIR"

echo ">> Consultando el catalogo de datos abiertos del Estado Peruano..."
# Python hace las peticiones HTTP por su cuenta. No se le pasa curl por tuberia:
# este bloque no lee stdin, y el pipe sin consumidor hace que curl falle con
# "Failure writing output to destination" al llenarse el buffer.
# Cada linea sale como "nombre_destino<TAB>url". El nombre lo decide Python y no
# el basename de la URL: en este portal los archivos vienen con %20 en el path y
# guardarlos crudos deja nombres con "%20" dentro.
URLS="$(python -c '
import json, sys, urllib.parse, urllib.request

sys.stdout.reconfigure(newline="\n")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BASE = "https://www.datosabiertos.gob.pe/api/3/action/"
DESTINOS = {
    "poblacion": "Poblacion_Identificada_RENIEC.csv",
    "ubigeos": "UBIGEO_2022_1891_distritos.xlsx",
}


def pedir(url):
    peticion = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(peticion, timeout=120))


# Este portal expone package_show pero no package_search, asi que la busqueda
# del nombre exacto se hace descargando el indice y filtrando en local.
ids = pedir(BASE + "package_list")["result"]
objetivos = {}
for identificador in ids:
    if identificador.startswith("rida-2026-poblaci"):
        objetivos["poblacion"] = identificador
    elif identificador.startswith("ubigeos-c"):
        objetivos["ubigeos"] = identificador

for etiqueta, identificador in objetivos.items():
    if etiqueta not in DESTINOS:
        continue
    respuesta = pedir(BASE + "package_show?" + urllib.parse.urlencode({"id": identificador}))
    paquete = respuesta["result"]
    if isinstance(paquete, list):
        paquete = paquete[0]
    for recurso in paquete["resources"]:
        formato = str(recurso.get("format") or "").lower()
        if etiqueta == "poblacion" and formato == "csv":
            print(DESTINOS[etiqueta] + "\t" + recurso["url"])
        if etiqueta == "ubigeos" and formato == "xlsx":
            print(DESTINOS[etiqueta] + "\t" + recurso["url"])
')"

if [ -z "$URLS" ]; then
  echo "ERROR: no se obtuvo ninguna URL del catalogo." >&2
  exit 1
fi

echo ">> Archivos a descargar: $(echo "$URLS" | wc -l)"
echo ">> Destino: $RAW_DIR"
echo ""

while IFS=$'\t' read -r name url; do
  [ -z "$url" ] && continue

  if [ -f "$RAW_DIR/$name" ]; then
    echo "   ya existe, se omite: $name"
    continue
  fi

  echo "   descargando: $name"
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
