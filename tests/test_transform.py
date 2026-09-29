"""Pruebas de la capa de transformacion. Corren sobre tests/fixtures/muestra.csv."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src import config, extract, transform

FIXTURE = Path(__file__).parent / "fixtures" / "muestra.csv"


@pytest.fixture()
def crudo() -> pd.DataFrame:
    return pd.read_csv(FIXTURE, encoding="utf-8")


@pytest.fixture()
def limpio(crudo: pd.DataFrame) -> tuple[pd.DataFrame, transform.Incidencias]:
    return transform.limpiar(crudo)


def test_normalizar_quita_acentos_y_pasa_a_mayusculas() -> None:
    assert config.normalizar("Machu Picchu") == "MACHU PICCHU"
    assert config.normalizar("  Puno  ") == "PUNO"
    assert config.normalizar("Ñ") == "N"
    assert config.normalizar("30 - 59 AÑOS") == "30 - 59 ANOS"


def test_columna_con_enie_se_renombra_a_anio(crudo: pd.DataFrame) -> None:
    columnas = list(config.normalizar_columnas(crudo).columns)
    assert "ANIO" in columnas
    assert "AÑO" not in columnas


def test_limpiar_no_pierde_filas(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert len(df) == incidencias["filas"] == 10


def test_niveles_fuera_de_catalogo_van_a_desconocido(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["nivel_no_valido"] == 2
    assert set(df["NIVEL_EESS"]) <= set(config.NIVELES_VALIDOS) | {config.NIVEL_DESCONOCIDO}
    assert (df["NIVEL_EESS"] == config.NIVEL_DESCONOCIDO).sum() == 2


def test_ubigeo_ilegible_y_ausente_terminan_en_centinela(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["ubigeo_invalido"] == 3
    assert (df["UBIGEO_DISTRITO"] == config.UBIGEO_DESCONOCIDO).sum() == 3


def test_ubigeo_conserva_el_cero_inicial(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    """010101 es Amazonas/Chachapoyas. Como entero perderia el cero y no cruzaria."""
    df, _ = limpio
    codigos = set(df["UBIGEO_DISTRITO"])
    assert "010101" in codigos
    assert "10101" not in codigos
    validos = codigos - {config.UBIGEO_DESCONOCIDO}
    assert all(len(c) == 6 and c.isdigit() for c in validos)


def test_sexo_fuera_de_catalogo_va_a_desconocido(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["sexo_no_valido"] == 1


def test_grupo_edad_fuera_de_catalogo_va_a_no_catalogado(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["grupo_no_catalogado"] == 1
    assert "NO CATALOGADO" in set(df["GRUPO_EDAD"])


def test_atenciones_no_numericas_se_cuentan_y_no_rompen_el_tipo(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["atenciones_no_numericas"] == 1
    assert df["ATENCIONES"].dtype == "int64"
    assert (df["ATENCIONES"] >= 0).all()


def test_suma_de_atenciones_se_conserva(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, incidencias = limpio
    assert incidencias["atenciones_totales"] == df["ATENCIONES"].sum()


def test_agregar_reduce_al_grano_del_hecho(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, _ = limpio
    hecho = transform.agregar_hecho(df)
    assert list(hecho.columns) == config.GRANO_HECHO + ["ATENCIONES"]
    assert hecho["ATENCIONES"].sum() == df["ATENCIONES"].sum()
    assert not hecho.duplicated(subset=config.GRANO_HECHO).any()


def test_dimension_nivel_reserva_la_clave_cero(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, _ = limpio
    nivel = transform.construir_dim_nivel(df["NIVEL_EESS"])
    assert nivel.loc[nivel["id_nivel"] == 0, "codigo"].item() == config.NIVEL_DESCONOCIDO
    assert (nivel["codigo"] != config.NIVEL_DESCONOCIDO).sum() == 3
    assert not nivel["codigo"].duplicated().any()


def test_dimension_grupo_edad_siempre_cubre_el_catalogo(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, _ = limpio
    grupo = transform.construir_dim_grupo_edad(df["GRUPO_EDAD"])
    assert set(config.ORDEN_GRUPO_EDAD) <= set(grupo["descripcion"])
    assert grupo.loc[grupo["id_grupo_edad"] == 0, "descripcion"].item() == "NO CATALOGADO"


def test_claves_de_dimension_no_se_repiten(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, _ = limpio
    territorio = transform.construir_dim_territorio(df)
    tiempo = transform.construir_dim_tiempo(df)
    sexo = transform.construir_dim_sexo(df["SEXO"])
    assert not territorio["ubigeo_distrito"].duplicated().any()
    assert not tiempo.duplicated(subset=["anio", "mes"]).any()
    assert not sexo["codigo"].duplicated().any()
    assert territorio["id_territorio"].min() == 1
    assert tiempo["id_tiempo"].min() == 1


def test_el_hecho_no_deja_orphan_keys(limpio: tuple[pd.DataFrame, transform.Incidencias]) -> None:
    df, _ = limpio
    hecho = transform.agregar_hecho(df)
    nivel = transform.construir_dim_nivel(df["NIVEL_EESS"])
    grupo = transform.construir_dim_grupo_edad(df["GRUPO_EDAD"])
    sexo = transform.construir_dim_sexo(df["SEXO"])
    territorio = transform.construir_dim_territorio(df)
    assert set(hecho["NIVEL_EESS"]) <= set(nivel["codigo"])
    assert set(hecho["GRUPO_EDAD"]) <= set(grupo["descripcion"])
    assert set(hecho["SEXO"]) <= set(sexo["codigo"])
    assert set(hecho["UBIGEO_DISTRITO"]) <= set(territorio["ubigeo_distrito"])


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("OPENDATA_DS_01_2017_ATENCIONES_0.zip", (2017, None)),
        ("OPENDATA_DS_01_2020_ATENCIONES_0.zip", (2020, None)),
        ("OPENDATA_DS_01_2021_01_06_ATENCIONES_0.zip", (2021, "01-06")),
        ("OPENDATA_DS_01_2025_07_12_ATENCIONES.zip", (2025, "07-12")),
    ],
)
def test_periodo_se_deduce_del_nombre(nombre: str, esperado: tuple[int, str | None]) -> None:
    assert extract.periodo_de(nombre) == esperado


def test_periodo_rechaza_nombres_que_no_son_del_dataset() -> None:
    with pytest.raises(ValueError):
        extract.periodo_de("otro_archivo.zip")
