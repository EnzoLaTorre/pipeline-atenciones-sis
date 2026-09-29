"""Pruebas de la dimension de poblacion (RENIEC/RIDA)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src import config, poblacion

FIXTURE = Path(__file__).parent / "fixtures" / "poblacion_rida.csv"


@pytest.fixture()
def resultado() -> tuple[pd.DataFrame, dict]:
    return poblacion.leer_poblacion(ruta=FIXTURE, lote=10)


def test_rellena_el_cero_inicial_del_ubigeo(resultado) -> None:
    """RIDA trae 10101 (5 digitos). Sin rellenar no cruzaria con 010101."""
    datos, _ = resultado
    assert "010101" in set(datos["ubigeo_distrito"])
    assert "10101" not in set(datos["ubigeo_distrito"])


def test_no_inventa_codigo_para_el_espacio_en_blanco(resultado) -> None:
    """Un espacio en blanco rellenado a 6 digitos se vuelve 000000, que parece
    un distrito real. Tiene que quedar fuera de la dimension y reportarse."""
    datos, descartes = resultado
    assert config.UBIGEO_DESCONOCIDO not in set(datos["ubigeo_distrito"])
    # Dos filas en blanco (999 + 888) mas una con codigo no numerico (42).
    assert descartes["poblacion_sin_codigo_ubigeo"] == 999 + 888 + 42
    assert descartes["filas_sin_codigo_ubigeo"] == 3


def test_descarta_codigos_no_numericos(resultado) -> None:
    datos, _ = resultado
    assert "ABCDEF" not in set(datos["ubigeo_distrito"])


def test_suma_las_filas_del_mismo_distrito(resultado) -> None:
    datos, _ = resultado
    fila = datos[datos["ubigeo_distrito"] == "010101"].iloc[0]
    assert fila["poblacion"] == 150


def test_todos_los_codigos_validos_tienen_seis_digitos(resultado) -> None:
    datos, _ = resultado
    assert all(len(c) == 6 and c.isdigit() for c in datos["ubigeo_distrito"])


def test_avisa_si_falta_el_archivo() -> None:
    with pytest.raises(FileNotFoundError):
        poblacion.leer_poblacion(ruta=Path("no_existe.csv"))
