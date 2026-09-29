"""Poblacion identificada por distrito (RENIEC, via RIDA).

Convierte el archivo de RIDA, que viene a nivel de persona, en una dimension de
poblacion por distrito utilizable como denominador de las tasas.

Dos advertencias que condicionan el uso de este dato:

- Es poblacion *identificada* con DNI a 2025, no poblacion residente. Un distrito
  con vielen residentes no censados aparece subrepresentado.
- Es un corte de un solo anio. Sirve para tasas de 2025, pero no para una serie
  de tasas: aplicar el denominador de 2025 a atenciones de 2017 seria invalido.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config

RUTA_RIDA = config.RAW_DIR / "Poblacion_Identificada_RENIEC.csv"
RUTA_UBIGEO = config.RAW_DIR / "UBIGEO_2022_1891_distritos.xlsx"

# El archivo trae dos codigos y no son intercambiables: en la muestra, RENIEC e
# INEI difieren en el 84% de las filas. El SIS usa codigos INEI, asi que el cruce
# tiene que hacerse por UBIGEO_INEI.
COLUMNA_UBIGEO = "UBIGEO_INEI"
COLUMNA_CANTIDAD = "Cantidad"


def leer_poblacion(
    ruta: Path = RUTA_RIDA, lote: int = 1_000_000
) -> tuple[pd.DataFrame, dict]:
    """Agrega el archivo de RIDA a poblacion por distrito. Lee en lotes.

    Devuelve la dimension y un diccionario de descartes. RIDA trae 1,310,465
    registros cuyo UBIGEO_INEI es un espacio en blanco; si se rellena a six
    digitos se convierte en el codigo 000000, que parece un distrito real. Se
    separan en vez de inventarles un codigo.
    """
    if not ruta.exists():
        raise FileNotFoundError(
            f"Falta {ruta.name}. Descargalo antes de correr el pipeline."
        )

    acumulado: dict[str, int] = {}
    sin_codigo_filas = 0
    sin_codigo_poblacion = 0

    for trozo in pd.read_csv(
        ruta,
        encoding="utf-8-sig",
        sep=",",
        usecols=[COLUMNA_UBIGEO, COLUMNA_CANTIDAD],
        chunksize=lote,
        low_memory=False,
    ):
        codigo = trozo[COLUMNA_UBIGEO].astype("string").str.strip()
        cantidad = pd.to_numeric(trozo[COLUMNA_CANTIDAD], errors="coerce").fillna(0)

        # RIDA guarda el UBIGEO sin el cero inicial (010101 llega como 10101), y
        # una parte de los registros lo trae como un espacio en blanco. Solo se
        # rellena a 6 digitos lo que es numerico: rellenar en blanco inventaria
        # el codigo 000000.
        numerico = codigo.str.fullmatch(r"[0-9]{1,6}", na=False)
        valido = numerico & (codigo.str.len() > 0)
        sin_codigo_filas += int((~valido).sum())
        sin_codigo_poblacion += int(cantidad[~valido].sum())

        codigo = codigo.where(valido).str.zfill(6)
        parcial = (
            pd.DataFrame({"codigo": codigo[valido], "poblacion": cantidad[valido]})
            .groupby("codigo", as_index=False)["poblacion"]
            .sum()
        )
        for cod, pob in zip(parcial["codigo"], parcial["poblacion"]):
            acumulado[str(cod)] = acumulado.get(str(cod), 0) + int(pob)

    poblacion = pd.DataFrame(
        {"ubigeo_distrito": list(acumulado), "poblacion": list(acumulado.values())}
    )
    poblacion["poblacion"] = poblacion["poblacion"].astype("int64")
    poblacion = poblacion.sort_values("ubigeo_distrito").reset_index(drop=True)

    descartes = {
        "filas_sin_codigo_ubigeo": sin_codigo_filas,
        "poblacion_sin_codigo_ubigeo": sin_codigo_poblacion,
    }
    return poblacion, descartes


def leer_ubigeos(ruta: Path = RUTA_UBIGEO) -> pd.DataFrame:
    """Catalogo oficial de distritos, para validar nombres y codigos."""
    if not ruta.exists():
        raise FileNotFoundError(f"Falta {ruta.name}. Descargalo antes de correr el pipeline.")

    crudo = pd.read_excel(ruta, dtype={"IDDIST": str})
    return crudo.rename(
        columns={
            "IDDIST": "ubigeo_distrito",
            "NOMBDEP": "departamento_ubigeo",
            "NOMBPROV": "provincia_ubigeo",
            "NOMBDIST": "distrito_ubigeo",
        }
    )[["ubigeo_distrito", "departamento_ubigeo", "provincia_ubigeo", "distrito_ubigeo"]].assign(
        ubigeo_distrito=lambda d: d["ubigeo_distrito"].str.strip().str.zfill(6)
    )


def auditar_cobertura(territorio: pd.DataFrame, poblacion: pd.DataFrame) -> dict:
    """Cuantos distritos con atenciones del SIS tienen denominador de poblacion."""
    con_datos = territorio[territorio["ubigeo_distrito"] != config.UBIGEO_DESCONOCIDO]
    codigos_sis = set(con_datos["ubigeo_distrito"])
    codigos_pob = set(poblacion["ubigeo_distrito"])
    total_pais = int(poblacion["poblacion"].sum())
    con_denominador = codigos_sis & codigos_pob
    atenciones_sin_denominador = int(
        con_datos.loc[~con_datos["ubigeo_distrito"].isin(codigos_pob), "id_territorio"].nunique()
    )
    return {
        "distritos_sis": len(codigos_sis),
        "distritos_con_poblacion": len(codigos_pob),
        "distritos_cruzan": len(con_denominador),
        "distritos_sis_sin_denominador": atenciones_sin_denominador,
        "poblacion_total_identificada": total_pais,
    }


def por_region(poblacion: pd.DataFrame, territorio: pd.DataFrame) -> pd.DataFrame:
    """Suma la poblacion de distrito a nivel de region.

    Es la dimension que usa el dashboard para convertir conteos en tasas. La
    poblacion es un atributo de la dimension territorial, no una medida del
    hecho, asi que no se guarda en el hecho: se guarda aparte y se cruza.
    """
    cruce = territorio.merge(poblacion, on="ubigeo_distrito", how="left")
    cruce = cruce[cruce["ubigeo_distrito"] != config.UBIGEO_DESCONOCIDO]
    resumen = (
        cruce.groupby("region", as_index=False)
        .agg(poblacion=("poblacion", "sum"), distritos=("ubigeo_distrito", "nunique"))
        .sort_values("region")
        .reset_index(drop=True)
    )
    resumen["poblacion"] = resumen["poblacion"].fillna(0).astype("int64")
    resumen["distritos"] = resumen["distritos"].astype("int32")
    return resumen


def guardar(poblacion: pd.DataFrame) -> Path:
    destino = config.PROCESSED_DIR / "dim_poblacion.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    poblacion.to_parquet(destino, index=False)
    return destino


def guardar_por_region(poblacion: pd.DataFrame, territorio: pd.DataFrame) -> Path:
    destino = config.PROCESSED_DIR / "dim_poblacion_region.parquet"
    por_region(poblacion, territorio).to_parquet(destino, index=False)
    return destino
