"""Configuracion central del pipeline ETL de atenciones del SIS."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
REPORTS_DIR = PROJECT_ROOT / "data" / "reports"

load_dotenv(PROJECT_ROOT / ".env")

PATRON_ZIP = "OPENDATA_DS_01_*_ATENCIONES*.zip"
TAMANO_LOTE = 250_000
ID_DESCONOCIDO = 0

COLUMNAS_ORIGEN = [
    "AÑO",
    "MES",
    "REGION",
    "PROVINCIA",
    "UBIGEO_DISTRITO",
    "DISTRITO",
    "COD_UNIDAD_EJECUTORA",
    "DESC_UNIDAD_EJECUTORA",
    "COD_IPRESS",
    "IPRESS",
    "NIVEL_EESS",
    "PLAN_SEGURO",
    "COD_SERVICIO",
    "DESC_SERVICIO",
    "SEXO",
    "GRUPO_EDAD",
    "ATENCIONES",
]

COLUMNAS_CANONICAS = [
    "ANIO",
    "MES",
    "REGION",
    "PROVINCIA",
    "UBIGEO_DISTRITO",
    "DISTRITO",
    "COD_UNIDAD_EJECUTORA",
    "DESC_UNIDAD_EJECUTORA",
    "COD_IPRESS",
    "IPRESS",
    "NIVEL_EESS",
    "PLAN_SEGURO",
    "COD_SERVICIO",
    "DESC_SERVICIO",
    "SEXO",
    "GRUPO_EDAD",
    "ATENCIONES",
]

NIVELES_VALIDOS = ("I", "II", "III")
SEXOS_VALIDOS = ("FEMENINO", "MASCULINO")
NIVEL_DESCONOCIDO = "DESCONOCIDO"

GRANO_HECHO = ["ANIO", "MES", "UBIGEO_DISTRITO", "NIVEL_EESS", "GRUPO_EDAD", "SEXO"]

# El grano del hecho solo necesita estas columnas. Las otras siete
# (COD_UNIDAD_EJECUTORA, DESC_UNIDAD_EJECUTORA, COD_IPRESS, IPRESS, PLAN_SEGURO,
# COD_SERVICIO, DESC_SERVICIO) se agregan dentro del grano y por eso nunca se
# materializan: leerlas y normalizarlas costaria tiempo y memoria sin efecto en
# el resultado. Para agregar una vista por prestacion (servicio CIE) o por
# establecimiento hay que ampliar esta lista y el grano del hecho.
COLUMNAS_NECESARIAS = [
    "AÑO",
    "MES",
    "REGION",
    "PROVINCIA",
    "UBIGEO_DISTRITO",
    "DISTRITO",
    "NIVEL_EESS",
    "SEXO",
    "GRUPO_EDAD",
    "ATENCIONES",
]

ORDEN_GRUPO_EDAD = {
    "00 - 04 ANOS": 1,
    "05 - 11 ANOS": 2,
    "12 - 17 ANOS": 3,
    "18 - 29 ANOS": 4,
    "30 - 59 ANOS": 5,
    "60 - MAS ANOS": 6,
}

ETIQUETA_NIVEL = {
    "I": "Primer nivel",
    "II": "Segundo nivel",
    "III": "Tercer nivel",
    NIVEL_DESCONOCIDO: "No registrado",
}

ETIQUETA_SEXO = {
    "FEMENINO": "Femenino",
    "MASCULINO": "Masculino",
    "DESCONOCIDO": "No registrado",
}


def normalizar(texto: object) -> str:
    """Pasa a mayusculas, quita acentos y colapsa espacios."""
    descompuesto = unicodedata.normalize("NFKD", str(texto).strip())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos).upper()


def mapeo_columnas() -> dict[str, str]:
    """Nombre origen normalizado -> nombre canonico. Fuente unica de verdad."""
    return {normalizar(origen): destino for origen, destino in zip(COLUMNAS_ORIGEN, COLUMNAS_CANONICAS)}


def columnas_necesarias_canonicas() -> list[str]:
    mapeo = mapeo_columnas()
    return [mapeo[normalizar(c)] for c in COLUMNAS_NECESARIAS]


def normalizar_columnas(frame: pd.DataFrame) -> pd.DataFrame:
    """Renombra a los nombres canonicos. Sin tilde, sin acentos, en mayusculas."""
    mapeo = mapeo_columnas()
    renombrado = {c: mapeo.get(normalizar(c), normalizar(c)) for c in frame.columns}
    return frame.rename(columns=renombrado)


def url_sqlserver() -> URL:
    usuario = os.getenv("SQLSERVER_USER") or None
    clave = os.getenv("SQLSERVER_PASSWORD") or None
    return URL.create(
        drivername="mssql+pymssql",
        username=usuario,
        password=clave,
        host=os.getenv("SQLSERVER_HOST", "localhost\\SQLEXPRESS"),
        database=os.getenv("SQLSERVER_DATABASE", "sis_atenciones"),
    )
