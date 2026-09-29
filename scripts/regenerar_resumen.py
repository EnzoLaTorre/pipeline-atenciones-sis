#!/usr/bin/env python
"""Regenera los artefactos chicos de data/processed/ desde los snapshots ya hechos.

El pipeline completo tarda ~35 minutos porque recorre los 1.6 GB de CSV. Este
script reconstruye solo los archivos que alimentan la demo en linea, leyendo los
Parquet que el pipeline ya dejo, para poder iterar sin reprocesar la fuente.

Regenera:
  - resumen_regional.parquet        (conteos por region, anio, nivel, grupo, sexo)
  - dim_poblacion.parquet           (poblacion identificada por distrito)
  - dim_poblacion_region.parquet    (poblacion por region, para las tasas)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import load, poblacion  # noqa: E402

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

    try:
        poblacion_distrito, descartes = poblacion.leer_poblacion()
    except FileNotFoundError as aviso:
        print(f"Poblacion no disponible, se sigue sin tasas: {aviso}")
        return 0

    cobertura = poblacion.auditar_cobertura(dimensiones["dim_territorio"], poblacion_distrito)
    destino_pob = poblacion.guardar(poblacion_distrito)
    destino_region = poblacion.guardar_por_region(
        poblacion_distrito, dimensiones["dim_territorio"]
    )

    print(
        f"{destino_pob.relative_to(RAIZ)}  {len(poblacion_distrito):,} distritos  "
        f"{cobertura['poblacion_total_identificada']:,} identificados"
    )
    print(f"{destino_region.relative_to(RAIZ)}  {len(pd.read_parquet(destino_region)):,} regiones")
    print(
        f"  {cobertura['distritos_cruzan']:,} de {cobertura['distritos_sis']:,} distritos del "
        f"SIS tienen denominador de poblacion"
    )
    print(
        f"  {descartes['poblacion_sin_codigo_ubigeo']:,} personas sin UBIGEO, excluidas "
        f"({descartes['filas_sin_codigo_ubigeo']:,} filas)"
    )
    if cobertura["distritos_sis_sin_denominador"]:
        print(
            f"  {cobertura['distritos_sis_sin_denominador']:,} distritos del SIS quedan sin "
            f"tasa: no aparecen en RIDA"
        )

    # El informe de calidad lo escribe el pipeline, que en esta corrida todavia no
    # conocia la poblacion. Se le anade el bloque en vez de sobrecribirlo, para que
    # el reporte versionado describa los artefactos que hay en disco.
    informe_destino = RAIZ / "data" / "reports" / "calidad.json"
    if informe_destino.exists():
        informe = json.loads(informe_destino.read_text(encoding="utf-8"))
        informe["poblacion"] = {**cobertura, **descartes}
        informe_destino.write_text(
            json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{informe_destino.relative_to(RAIZ)}  bloque de poblacion actualizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
