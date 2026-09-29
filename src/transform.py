"""Limpieza, tipado y agregacion al grano del modelo estrella."""
from __future__ import annotations

import pandas as pd

from . import config


class Incidencias(dict):
    """Acumula contadores de calidad durante la limpieza de un lote."""

    def __init__(self) -> None:
        super().__init__(
            filas=0,
            ubigeo_invalido=0,
            sin_ubigeo=0,
            nivel_no_valido=0,
            sexo_no_valido=0,
            grupo_no_catalogado=0,
            atenciones_no_numericas=0,
            atenciones_nulas=0,
            atenciones_negativas=0,
            atenciones_totales=0,
        )

    def sumar(self, otra: "Incidencias") -> None:
        for clave, valor in otra.items():
            self[clave] += valor


def limpiar(frame: pd.DataFrame) -> tuple[pd.DataFrame, Incidencias]:
    """Normaliza un lote crudo y devuelve el lote limpio con sus incidencias."""
    incidencias = Incidencias()
    df = config.normalizar_columnas(frame)

    requeridas = config.columnas_necesarias_canonicas()
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(f"El archivo no tiene las columnas esperadas: {faltantes}")

    incidencias["filas"] = len(df)

    for columna in df.select_dtypes(include=["string", "object"]).columns:
        df[columna] = df[columna].str.strip()

    ubigeo = pd.to_numeric(df["UBIGEO_DISTRITO"], errors="coerce")
    sin_ubigeo = ubigeo.isna()
    incidencias["ubigeo_invalido"] = int(sin_ubigeo.sum())
    incidencias["sin_ubigeo"] = incidencias["ubigeo_invalido"]
    df["UBIGEO_DISTRITO"] = ubigeo.fillna(0).astype("int64").astype(str).str.zfill(6)
    df.loc[sin_ubigeo, "UBIGEO_DISTRITO"] = config.UBIGEO_DESCONOCIDO

    df["NIVEL_EESS"] = df["NIVEL_EESS"].fillna("").map(config.normalizar)
    nivel_malo = ~df["NIVEL_EESS"].isin(config.NIVELES_VALIDOS)
    incidencias["nivel_no_valido"] = int(nivel_malo.sum())
    df.loc[nivel_malo, "NIVEL_EESS"] = config.NIVEL_DESCONOCIDO

    df["SEXO"] = df["SEXO"].fillna("").map(config.normalizar)
    sexo_malo = ~df["SEXO"].isin(config.SEXOS_VALIDOS)
    incidencias["sexo_no_valido"] = int(sexo_malo.sum())
    df.loc[sexo_malo, "SEXO"] = "DESCONOCIDO"

    df["GRUPO_EDAD"] = df["GRUPO_EDAD"].fillna("").map(config.normalizar)
    grupo_malo = ~df["GRUPO_EDAD"].isin(config.ORDEN_GRUPO_EDAD)
    incidencias["grupo_no_catalogado"] = int(grupo_malo.sum())
    df.loc[grupo_malo, "GRUPO_EDAD"] = "NO CATALOGADO"

    atenciones = pd.to_numeric(df["ATENCIONES"], errors="coerce")
    incidencias["atenciones_no_numericas"] = int((df["ATENCIONES"].notna() & atenciones.isna()).sum())
    incidencias["atenciones_nulas"] = int(atenciones.isna().sum())
    incidencias["atenciones_negativas"] = int((atenciones < 0).sum())
    incidencias["atenciones_totales"] = int(atenciones.fillna(0).sum())
    df["ATENCIONES"] = atenciones.fillna(0).astype("int64")

    for columna in ("ANIO", "MES"):
        df[columna] = pd.to_numeric(df[columna], errors="coerce").fillna(0).astype("int32")

    df["REGION"] = df["REGION"].fillna("").map(config.normalizar)
    df["PROVINCIA"] = df["PROVINCIA"].fillna("").map(config.normalizar)
    df["DISTRITO"] = df["DISTRITO"].fillna("").map(config.normalizar)

    return df, incidencias


def agregar_hecho(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce el lote al grano distrito-mes-nivel-edad-sexo."""
    return (
        df.groupby(config.GRANO_HECHO, as_index=False, observed=True)["ATENCIONES"]
        .sum()
        .sort_values(config.GRANO_HECHO)
        .reset_index(drop=True)
    )


def construir_dim_tiempo(periodos: pd.DataFrame) -> pd.DataFrame:
    unicos = periodos[["ANIO", "MES"]].drop_duplicates().sort_values(["ANIO", "MES"])
    return pd.DataFrame(
        {
            "id_tiempo": range(1, len(unicos) + 1),
            "anio": unicos["ANIO"].to_numpy(),
            "mes": unicos["MES"].to_numpy(),
        }
    )


def construir_dim_territorio(territorios: pd.DataFrame) -> pd.DataFrame:
    unicos = territorios[["UBIGEO_DISTRITO", "REGION", "PROVINCIA", "DISTRITO"]].drop_duplicates(
        subset=["UBIGEO_DISTRITO"]
    )
    unicos = unicos.sort_values("UBIGEO_DISTRITO").reset_index(drop=True)
    return pd.DataFrame(
        {
            "id_territorio": range(1, len(unicos) + 1),
            "ubigeo_distrito": unicos["UBIGEO_DISTRITO"].to_numpy(),
            "region": unicos["REGION"].replace("", "NO REGISTRADO").to_numpy(),
            "provincia": unicos["PROVINCIA"].replace("", "NO REGISTRADO").to_numpy(),
            "distrito": unicos["DISTRITO"].replace("", "NO REGISTRADO").to_numpy(),
        }
    )


def construir_dim_nivel(niveles: pd.Series) -> pd.DataFrame:
    otros = sorted(v for v in set(niveles) if v != config.NIVEL_DESCONOCIDO)
    filas = [{"id_nivel": 0, "codigo": config.NIVEL_DESCONOCIDO, "etiqueta": config.ETIQUETA_NIVEL[config.NIVEL_DESCONOCIDO], "orden": 0}]
    filas += [
        {"id_nivel": i + 1, "codigo": v, "etiqueta": config.ETIQUETA_NIVEL[v], "orden": i + 1}
        for i, v in enumerate(otros)
    ]
    return pd.DataFrame(filas)


def construir_dim_grupo_edad(grupos: pd.Series) -> pd.DataFrame:
    otros = sorted(
        (v for v in set(grupos) | set(config.ORDEN_GRUPO_EDAD) if v != "NO CATALOGADO"),
        key=lambda v: (config.ORDEN_GRUPO_EDAD.get(v, 99), v),
    )
    filas = [{"id_grupo_edad": 0, "descripcion": "NO CATALOGADO", "orden": 0}]
    filas += [
        {"id_grupo_edad": i + 1, "descripcion": v, "orden": config.ORDEN_GRUPO_EDAD.get(v, 99)}
        for i, v in enumerate(otros)
    ]
    return pd.DataFrame(filas)


def construir_dim_sexo(sexos: pd.Series) -> pd.DataFrame:
    otros = sorted(v for v in set(sexos) if v != "DESCONOCIDO")
    filas = [{"id_sexo": 0, "codigo": "DESCONOCIDO", "etiqueta": config.ETIQUETA_SEXO["DESCONOCIDO"]}]
    filas += [
        {"id_sexo": i + 1, "codigo": v, "etiqueta": config.ETIQUETA_SEXO.get(v, v)}
        for i, v in enumerate(otros)
    ]
    return pd.DataFrame(filas)
