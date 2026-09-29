"""Escritura del modelo estrella en SQL Server y del snapshot en Parquet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import config

TABLA_HECHO = "fact_atencion"
LOTE_INSERCION = 50_000


def motor() -> Engine:
    return create_engine(config.url_sqlserver(), pool_pre_ping=True, future=True)


def crear_base() -> None:
    """Crea la base destino si no existe. Se conecta a master para poder hacerlo."""
    base = config.url_sqlserver()
    maestro = base.set(database="master")
    sentencia = (
        f"IF DB_ID('{base.database}') IS NULL "
        f"CREATE DATABASE [{base.database}];"
    )
    with create_engine(maestro, isolation_level="AUTOCOMMIT", future=True).begin() as conexion:
        conexion.execute(text(sentencia))


def crear_esquema(motor_: Engine) -> None:
    ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with motor_.begin() as conexion:
        for sentencia in ddl.split(";"):
            if sentencia.strip():
                conexion.execute(text(sentencia))


def _resolver_claves(dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> pd.DataFrame:
    """Reemplaza las claves naturales de la tabla ancha por las claves surrogate."""
    hecho = hechos.merge(
        dimensiones["dim_tiempo"].rename(columns={"anio": "ANIO", "mes": "MES"})[["ANIO", "MES", "id_tiempo"]],
        on=["ANIO", "MES"],
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_territorio"].rename(columns={"ubigeo_distrito": "UBIGEO_DISTRITO"})[
            ["UBIGEO_DISTRITO", "id_territorio"]
        ],
        on="UBIGEO_DISTRITO",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_nivel"][["codigo", "id_nivel"]], left_on="NIVEL_EESS", right_on="codigo", how="left"
    )
    hecho = hecho.merge(
        dimensiones["dim_grupo_edad"][["descripcion", "id_grupo_edad"]],
        left_on="GRUPO_EDAD",
        right_on="descripcion",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_sexo"][["codigo", "id_sexo"]], left_on="SEXO", right_on="codigo", how="left"
    )

    columnas_clave = ["id_tiempo", "id_territorio", "id_nivel", "id_grupo_edad", "id_sexo"]
    for columna in columnas_clave:
        hecho[columna] = hecho[columna].fillna(config.ID_DESCONOCIDO).astype("int32")

    return hecho[columnas_clave + ["ATENCIONES"]].rename(columns={"ATENCIONES": "atenciones"})


def cargar(motor_: Engine, dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> int:
    """Reconstruye dimensiones y hecho. Es idempotente: reemplaza el contenido."""
    ancho = _resolver_claves(dimensiones, hechos)

    with motor_.begin() as conexion:
        conexion.execute(text(f"TRUNCATE TABLE dbo.{TABLA_HECHO}"))
        for nombre in ("dim_tiempo", "dim_territorio", "dim_nivel", "dim_grupo_edad", "dim_sexo"):
            conexion.execute(text(f"TRUNCATE TABLE dbo.{nombre}"))

    for nombre, tabla in dimensiones.items():
        tabla.to_sql(nombre, motor_, if_exists="append", index=False, chunksize=LOTE_INSERCION)

    total = 0
    for inicio in range(0, len(ancho), LOTE_INSERCION):
        trozo = ancho.iloc[inicio : inicio + LOTE_INSERCION]
        trozo.to_sql(TABLA_HECHO, motor_, if_exists="append", index=False, method="multi")
        total += len(trozo)
    return total


def _hecho_legible(dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> pd.DataFrame:
    """Agrega las etiquetas legibles de cada dimension al hecho."""
    hecho = hechos.merge(
        dimensiones["dim_territorio"][["ubigeo_distrito", "region"]].rename(
            columns={"ubigeo_distrito": "ubigeo"}
        ),
        left_on="UBIGEO_DISTRITO",
        right_on="ubigeo",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_nivel"][["codigo", "etiqueta"]].rename(columns={"etiqueta": "nivel"}),
        left_on="NIVEL_EESS",
        right_on="codigo",
        how="left",
    )
    hecho = hecho.merge(
        dimensiones["dim_grupo_edad"][["descripcion", "orden"]],
        left_on="GRUPO_EDAD",
        right_on="descripcion",
        how="left",
    )
    hecho = hecho.rename(columns={"descripcion": "grupo_edad", "orden": "orden_edad"})
    hecho = hecho.merge(
        dimensiones["dim_sexo"][["codigo", "etiqueta"]].rename(columns={"etiqueta": "sexo"}),
        left_on="SEXO",
        right_on="codigo",
        how="left",
    )
    return hecho


def escribir_resumen_regional(dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> Path:
    """Snapshot a grano region-mes, desnormalizado y con etiquetas.

    Es el archivo que se versiona y que lee la demo en linea: cabe completo en
    el repositorio y no necesita SQL Server para nada.
    """
    legible = _hecho_legible(dimensiones, hechos)
    resumen = (
        legible.groupby(
            ["ANIO", "MES", "region", "nivel", "orden_edad", "grupo_edad", "sexo"],
            as_index=False,
            observed=True,
        )["ATENCIONES"]
        .sum()
        .rename(
            columns={
                "ANIO": "anio",
                "MES": "mes",
                "ATENCIONES": "atenciones",
                "region": "region",
            }
        )
        .sort_values(["anio", "mes", "region", "nivel"])
        .reset_index(drop=True)
    )
    destino = config.PROCESSED_DIR / "resumen_regional.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    resumen.to_parquet(destino, index=False)
    return destino


def escribir_snapshot(dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> Path:
    """Snapshot agregado y pequeño que alimenta la demo en linea sin SQL Server."""
    destino = config.PROCESSED_DIR
    destino.mkdir(parents=True, exist_ok=True)
    ancho = _resolver_claves(dimensiones, hechos)
    for nombre, tabla in dimensiones.items():
        tabla.to_parquet(destino / f"{nombre}.parquet", index=False)
    ancho.to_parquet(destino / "fact_atencion.parquet", index=False)
    return destino / "fact_atencion.parquet"
