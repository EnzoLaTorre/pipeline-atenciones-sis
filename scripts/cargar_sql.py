"""Crea la base, el esquema y carga el modelo estrella desde los snapshots Parquet.

Permite tener SQL Server poblado sin repetir el ETL completo (~35 minutos), que
es lo que hace falta al iterar sobre el modelo o sobre consultas de analisis.

    python scripts/cargar_sql.py

Es idempotente: vacia las tablas del modelo y las vuelve a llenar, asi que se
puede correr las veces que haga falta.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, load  # noqa: E402


def _log(mensaje: str) -> None:
    print(mensaje, flush=True)


def main() -> int:
    inicio = time.perf_counter()

    _log("1/3  Creando la base si no existe")
    load.crear_base()

    motor_ = load.motor()
    _log("2/3  Aplicando el DDL")
    load.crear_esquema(motor_)

    _log("3/3  Cargando el modelo desde Parquet")
    filas = load.cargar_desde_parquet(motor_, progreso=_log)
    _log(f"     {filas:,} filas en dbo.{load.TABLA_HECHO}")

    _log("")
    _log("Verificacion contra los snapshots:")

    esperado_hecho = len(pd.read_parquet(config.PROCESSED_DIR / f"{load.TABLA_HECHO}.parquet"))
    esperado_aten = int(
        pd.read_parquet(config.PROCESSED_DIR / f"{load.TABLA_HECHO}.parquet")["atenciones"].sum()
    )

    with motor_.connect() as conexion:
        _verificar(conexion, "fact_atencion", "filas", esperado_hecho)
        _verificar(
            conexion,
            load.TABLA_HECHO,
            "SUM(atenciones)",
            esperado_aten,
            sentencia=f"SELECT SUM(atenciones) AS valor FROM dbo.{load.TABLA_HECHO}",
        )
        for nombre in load.DIMENSIONES + load.DIMENSIONES_POBLACION:
            ruta = config.PROCESSED_DIR / f"{nombre}.parquet"
            if not ruta.exists():
                _log(f"  --   {nombre:<22} sin Parquet, no se verifica")
                continue
            esperado = len(pd.read_parquet(ruta))
            _verificar(conexion, nombre, "filas", esperado)

        huerfanas = conexion.execute(
            text(
                "SELECT COUNT(*) FROM dbo.fact_atencion f "
                "LEFT JOIN dbo.dim_tiempo     t ON t.id_tiempo     = f.id_tiempo "
                "LEFT JOIN dbo.dim_territorio r ON r.id_territorio = f.id_territorio "
                "LEFT JOIN dbo.dim_nivel      n ON n.id_nivel      = f.id_nivel "
                "LEFT JOIN dbo.dim_grupo_edad g ON g.id_grupo_edad = f.id_grupo_edad "
                "LEFT JOIN dbo.dim_sexo       x ON x.id_sexo       = f.id_sexo "
                "WHERE t.id_tiempo IS NULL OR r.id_territorio IS NULL OR n.id_nivel IS NULL "
                "OR g.id_grupo_edad IS NULL OR x.id_sexo IS NULL"
            )
        ).scalar()

    print()
    if huerfanas == 0:
        _log(f"OK  Sin filas huerfanas. Carga completa en {time.perf_counter() - inicio:.1f} s.")
        return 0
    _log(f"FALLA  {huerfanas:,} filas del hecho no encuentran dimension.")
    return 1


def _verificar(conexion, tabla: str, etiqueta: str, esperado: int, sentencia: str | None = None) -> None:
    if sentencia is None:
        sentencia = f"SELECT COUNT(*) AS valor FROM dbo.{tabla}"
    valor = int(conexion.execute(text(sentencia)).scalar() or 0)
    marca = "OK  " if valor == esperado else "DIF "
    unidad = "" if etiqueta == "filas" else f" {etiqueta}"
    detalle = "" if valor == esperado else f"  (parquet: {esperado:,})"
    _log(f"  {marca} {tabla:<16} {valor:>15,}{unidad}{detalle}")


if __name__ == "__main__":
    raise SystemExit(main())
