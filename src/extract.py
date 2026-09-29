"""Lectura por lotes de los CSV comprimidos publicados por el Estado."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Iterator

import pandas as pd

from . import config

_REGEX_PERIODO = re.compile(r"_(\d{4})(?:_(\d{2})_(\d{2}))?_ATENCIONES")

DTIPOS_TEXTO = {
    "REGION": "string",
    "PROVINCIA": "string",
    "DISTRITO": "string",
    "UBIGEO_DISTRITO": "string",
    "COD_UNIDAD_EJECUTORA": "string",
    "DESC_UNIDAD_EJECUTORA": "string",
    "COD_IPRESS": "string",
    "IPRESS": "string",
    "NIVEL_EESS": "string",
    "PLAN_SEGURO": "string",
    "COD_SERVICIO": "string",
    "DESC_SERVICIO": "string",
    "SEXO": "string",
    "GRUPO_EDAD": "string",
}


def archivos_fuente() -> list[Path]:
    return sorted(config.RAW_DIR.glob(config.PATRON_ZIP))


def periodo_de(nombre: str) -> tuple[int, str | None]:
    """Deduce (anio, semestre) del nombre del archivo. Anio sin semestre es anual."""
    encontrado = _REGEX_PERIODO.search(nombre)
    if not encontrado:
        raise ValueError(f"No se pudo deducir el periodo de {nombre}")
    anio = int(encontrado.group(1))
    desde = encontrado.group(2)
    return anio, (f"{desde}-{encontrado.group(3)}" if desde else None)


def leer_lotes(ruta: Path, lote: int = config.TAMANO_LOTE) -> Iterator[pd.DataFrame]:
    """Itera el CSV dentro del ZIP sin descomprimirlo en disco ni en memoria."""
    with zipfile.ZipFile(ruta) as zip_:
        miembro = zip_.namelist()[0]
        with zip_.open(miembro) as flujo:
            lector = pd.read_csv(
                flujo,
                encoding="utf-8",
                sep=",",
                usecols=config.COLUMNAS_NECESARIAS,
                chunksize=lote,
                dtype=DTIPOS_TEXTO,
                low_memory=False,
            )
            for frame in lector:
                yield frame
