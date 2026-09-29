#!/usr/bin/env python
"""Regenera data/processed/resumen_regional.parquet desde los snapshots ya hechos.

El pipeline completo tarda ~35 minutos porque recorre los 1.6 GB de CSV. Este
script reconstruye solo el archivo chico que alimenta la demo en linea, leyendo
los Parquet que el pipeline ya dejo, para poder iterar sin reprocesar la fuente.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import load  # noqa: E402

ORIGEN = RAIZ / "data" / "processed"
DIMENSIONES = ["dim_tiempo", "dim_territorio", "dim_nivel", "dim_grupo_edad", "dim_sexo"]


def reconstruir_claves_naturales() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    dimensiones = {nombre: pd.read_parquet(ORIGEN / f"{nombre}.parquet") for nombre in DIMENSIONES}
    hecho = pd.read_parquet(ORIGEN / "fact_atencion.parquet")

    hecho = hecho.merge(
        dimensiones["dim_tiempo"][["id_tiempo", "anio", "mes"]], on="id_tiempo", how="left"
    )
    hecho = hecho.merge(
        dimensiones["dim_territorio"][["id_territorio", "ubigeo_distrito"]],
        on="id_territorio",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_nivel"][["id_nivel", "codigo"]].rename(
            columns={"codigo": "nivel_codigo"}
        ),
        on="id_nivel",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_grupo_edad"][["id_grupo_edad", "descripcion"]].rename(
            columns={"descripcion": "grupo_codigo"}
        ),
        on="id_grupo_edad",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_sexo"][["id_sexo", "codigo"]].rename(columns={"codigo": "sexo_codigo"}),
        on="id_sexo",
        how="left",
    )

    hechos = hecho.rename(
        columns={
            "anio": "ANIO",
            "mes": "MES",
            "ubigeo_distrito": "UBIGEO_DISTRITO",
            "grupo_codigo": "GRUPO_EDAD",
            "nivel_codigo": "NIVEL_EESS",
            "sexo_codigo": "SEXO",
            "atenciones": "ATENCIONES",
        }
    )
    return dimensiones, hechos[
        ["ANIO", "MES", "UBIGEO_DISTRITO", "NIVEL_EESS", "GRUPO_EDAD", "SEXO", "ATENCIONES"]
    ]


def main() -> int:
    faltantes = [n for n in DIMENSIONES if not (ORIGEN / f"{n}.parquet").exists()]
    if faltantes:
        print(f"Faltan snapshots: {faltantes}. Ejecuta `python -m src.pipeline` primero.")
        return 1

    dimensiones, hechos = reconstruir_claves_naturales()
    destino = load.escribir_resumen_regional(dimensiones, hechos)
    filas = len(pd.read_parquet(destino))
    print(f"{destino.relative_to(RAIZ)}  {filas:,} filas  {destino.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
