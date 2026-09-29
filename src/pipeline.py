"""Orquestador del pipeline: crudo (ZIP) -> estrella (SQL Server) + snapshot."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from . import config, extract, load, transform


def _log(mensaje: str) -> None:
    print(mensaje, flush=True)


def ejecutar(solo_parquet: bool, limite: int | None) -> int:
    archivos = extract.archivos_fuente()
    if limite:
        archivos = archivos[:limite]
    if not archivos:
        _log(f"No hay archivos en {config.RAW_DIR}. Ejecuta scripts/download_data.sh primero.")
        return 1

    inicio = time.perf_counter()
    incidencias = transform.Incidencias()
    trozos_hecho: list[pd.DataFrame] = []
    territorios = pd.DataFrame()
    periodos = pd.DataFrame()
    niveles: set[str] = set()
    grupos: set[str] = set()
    sexos: set[str] = set()
    detalle: list[dict] = []

    for archivo in archivos:
        anio, semestre = extract.periodo_de(archivo.name)
        tiempo_archivo = time.perf_counter()
        filas = 0
        for lote in extract.leer_lotes(archivo):
            limpio, incidencias_lote = transform.limpiar(lote)
            incidencias.sumar(incidencias_lote)
            filas += len(limpio)

            trozos_hecho.append(transform.agregar_hecho(limpio))

            muestra = limpio[["UBIGEO_DISTRITO", "REGION", "PROVINCIA", "DISTRITO"]]
            muestra = muestra.drop_duplicates(subset=["UBIGEO_DISTRITO"])
            territorios = pd.concat([territorios, muestra], ignore_index=True)

            periodos = pd.concat([periodos, limpio[["ANIO", "MES"]]], ignore_index=True)
            niveles |= set(limpio["NIVEL_EESS"].unique())
            grupos |= set(limpio["GRUPO_EDAD"].unique())
            sexos |= set(limpio["SEXO"].unique())

        detalle.append(
            {
                "archivo": archivo.name,
                "anio": anio,
                "semestre": semestre,
                "filas_crudas": filas,
                "segundos": round(time.perf_counter() - tiempo_archivo, 1),
            }
        )
        _log(
            f"  {archivo.name:48s} {anio} {semestre or 'anual':8s}"
            f" {filas:>10,} filas  {detalle[-1]['segundos']:>6.1f}s"
        )

    dimensiones = {
        "dim_tiempo": transform.construir_dim_tiempo(periodos),
        "dim_territorio": transform.construir_dim_territorio(territorios),
        "dim_nivel": transform.construir_dim_nivel(pd.Series(sorted(niveles))),
        "dim_grupo_edad": transform.construir_dim_grupo_edad(pd.Series(sorted(grupos))),
        "dim_sexo": transform.construir_dim_sexo(pd.Series(sorted(sexos))),
    }
    hechos = pd.concat(trozos_hecho, ignore_index=True)
    hechos = (
        hechos.groupby(config.GRANO_HECHO, as_index=False, observed=True)["ATENCIONES"]
        .sum()
        .reset_index(drop=True)
    )

    _log("")
    _log(f"Hecho agregado: {len(hechos):,} filas  |  atenciones: {hechos['ATENCIONES'].sum():,}")
    for nombre, tabla in dimensiones.items():
        _log(f"  {nombre:20s} {len(tabla):>6,} filas")

    ruta = load.escribir_snapshot(dimensiones, hechos)
    _log(f"Snapshot: {ruta.relative_to(config.PROJECT_ROOT)}")
    resumen = load.escribir_resumen_regional(dimensiones, hechos)
    _log(
        f"Resumen regional: {resumen.relative_to(config.PROJECT_ROOT)}"
        f" ({resumen.stat().st_size / 1024:.0f} KB)"
    )

    if solo_parquet:
        _log("Listo (--solo-parquet: no se toco SQL Server).")
    else:
        load.crear_base()
        motor_ = load.motor()
        load.crear_esquema(motor_)
        cargadas = load.cargar(motor_, dimensiones, hechos)
        _log(f"SQL Server: {cargadas:,} filas en dbo.fact_atencion")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    informe = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archivos": detalle,
        "incidencias": dict(incidencias),
        "filas_hecho": int(len(hechos)),
        "atenciones_totales": int(hechos["ATENCIONES"].sum()),
        "segundos_total": round(time.perf_counter() - inicio, 1),
    }
    destino = config.REPORTS_DIR / "calidad.json"
    destino.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    _log(f"Informe de calidad: {destino.relative_to(config.PROJECT_ROOT)}")
    _log(f"Tiempo total: {informe['segundos_total']}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline ETL de atenciones del SIS")
    parser.add_argument("--solo-parquet", action="store_true", help="No escribe en SQL Server")
    parser.add_argument("--limite", type=int, default=None, help="Procesa solo los primeros N archivos")
    argumentos = parser.parse_args()
    return ejecutar(argumentos.solo_parquet, argumentos.limite)


if __name__ == "__main__":
    sys.exit(main())
