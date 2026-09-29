"""Dashboard de atenciones del SIS.

Lee data/processed/resumen_regional.parquet, que el pipeline deja versionado.
No necesita SQL Server ni conexion a la red: es lo que corre en Streamlit Cloud.
"""
from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Atenciones del SIS", page_icon="🇵🇪", layout="wide")

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DATOS = RAIZ / "data" / "processed" / "resumen_regional.parquet"
RUTA_POBLACION = RAIZ / "data" / "processed" / "dim_poblacion_region.parquet"

ORDEN_NIVEL = ["Primer nivel", "Segundo nivel", "Tercer nivel", "No registrado"]
COLORES_NIVEL = ["#2E7D6F", "#E9A13B", "#B3453C", "#9AA5A1"]
RANGO_RECOMENDADO = (70, 80)
FUENTE_REFERENCIA = (
    "MinSa, RM 464-2011/MINSA: el primer nivel \"podría resolverse localmente entre "
    "el 70 y el 80% de las necesidades básicas más frecuentes\"."
)
ANIO_TASAS = 2025


@st.cache_data
def cargar() -> pd.DataFrame:
    return pd.read_parquet(RUTA_DATOS)


@st.cache_data
def cargar_poblacion() -> pd.DataFrame | None:
    if not RUTA_POBLACION.exists():
        return None
    return pd.read_parquet(RUTA_POBLACION)


@st.cache_data
def disponibles(df: pd.DataFrame) -> tuple[list[int], list[str], list[str], list[str]]:
    return (
        sorted(df["anio"].unique()),
        sorted(df["region"].unique()),
        sorted(df["grupo_edad"].unique()),
        sorted(df["sexo"].unique()),
    )


st.title("Como se reparten las atenciones del SIS por nivel de atencion")
st.caption(
    "Fuente: Datos Abiertos del Estado Peruano, dataset *Atenciones realizadas a los "
    "asegurados - SIS* (licencia ODC-By). Cada punto es el conteo de atenciones "
    "reportadas por establecimiento, agregado desde el detalle oficial."
)

if not RUTA_DATOS.exists():
    st.error(
        f"No existe {RUTA_DATOS.name}. Ejecuta `python -m src.pipeline` para generarlo."
    )
    st.stop()

datos = cargar()
anios, regiones, grupos, sexos = disponibles(datos)

barra_lateral = st.sidebar
barra_lateral.header("Filtros")
rango_anios = barra_lateral.select_slider(
    "Periodo", options=anios, value=(anios[0], anios[-1])
)
region = barra_lateral.multiselect("Region", regiones, default=[])
grupo = barra_lateral.multiselect("Grupo de edad", grupos, default=[])
sexo = barra_lateral.multiselect("Sexo", sexos, default=[])

vista = datos[
    datos["anio"].between(*rango_anios)
    & datos["region"].isin(region or regiones)
    & datos["grupo_edad"].isin(grupo or grupos)
    & datos["sexo"].isin(sexo or sexos)
].copy()

if vista.empty:
    st.warning("No hay datos para los filtros seleccionados.")
    st.stop()

total = int(vista["atenciones"].sum())
vista["nivel"] = pd.Categorical(vista["nivel"], categories=ORDEN_NIVEL, ordered=True)

por_nivel = vista.groupby("nivel", observed=True)["atenciones"].sum()
primer_nivel = int(por_nivel.get("Primer nivel", 0))
porcentaje_primer = primer_nivel / total * 100 if total else 0.0

col1, col2, col3 = st.columns(3)
col1.metric("Atenciones", f"{total:,}")
col2.metric("En primer nivel", f"{porcentaje_primer:.1f} %")
col3.metric(
    "Referencia MinSa",
    f"{RANGO_RECOMENDADO[0]}-{RANGO_RECOMENDADO[1]} %",
    delta=f"{porcentaje_primer - RANGO_RECOMENDADO[0]:+.1f} pp vs. piso",
    delta_color="off",
)

st.subheader("Como se movio el reparto entre niveles")
serie = vista.groupby(["anio", "nivel"], observed=True)["atenciones"].sum().reset_index()
totales_anio = serie.groupby("anio")["atenciones"].sum().rename("total")
serie = serie.merge(totales_anio, on="anio")
serie["porcentaje"] = serie["atenciones"] / serie["total"] * 100

linea = (
    alt.Chart(serie)
    .mark_line(point=True, strokeWidth=2.5)
    .encode(
        x=alt.X("anio:O", title="Anio", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("porcentaje:Q", title="% del total de atenciones", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("nivel:N", title="Nivel", scale=alt.Scale(domain=ORDEN_NIVEL, range=COLORES_NIVEL)),
        tooltip=[
            alt.Tooltip("anio:O", title="Anio"),
            alt.Tooltip("nivel:N", title="Nivel"),
            alt.Tooltip("atenciones:Q", title="Atenciones", format=","),
            alt.Tooltip("porcentaje:Q", title="% del total", format=".2f"),
        ],
    )
    .properties(height=380)
)

banda = (
    alt.Chart(pd.DataFrame({"y": [RANGO_RECOMENDADO[0], RANGO_RECOMENDADO[1]]}))
    .mark_rule(strokeDash=[6, 4], color="#666", strokeWidth=1)
    .encode(y="y:Q")
)

st.altair_chart(banda + linea, width="stretch")
st.caption(f"La banda punteada marca el rango de referencia de {FUENTE_REFERENCIA}")
st.warning(
    "La referencia y esta serie no miden exactamente lo mismo. El MinSa estima qué "
    "proporción de las *necesidades de salud* se resuelve localmente; el SIS cuenta "
    "*atenciones registradas* por nivel. El segundo depende de a dónde acude la gente, "
    "y el financiamiento per cápita al primer nivel incentiva a los "
    "establecimientos a diagnosticar y derivar, no a resolver. Leer el gap como "
    "\"ineficiencia\" sería una sobreinterpretación."
)

st.subheader("Por region, en el ultimo ano del periodo")
ultimo = int(rango_anios[1])
regional = (
    vista[vista["anio"] == ultimo]
    .groupby(["region", "nivel"], observed=True)["atenciones"]
    .sum()
    .reset_index()
)
total_regional = regional.groupby("region")["atenciones"].transform("sum")
regional["porcentaje"] = regional["atenciones"] / total_regional * 100

barras = (
    alt.Chart(regional)
    .mark_bar()
    .encode(
            x=alt.X("porcentaje:Q", title="% de atenciones", stack="normalize"),
        y=alt.Y("region:N", title="Region", sort="-x"),
        color=alt.Color(
            "nivel:N", title="Nivel", scale=alt.Scale(domain=ORDEN_NIVEL, range=COLORES_NIVEL)
        ),
        tooltip=[
            alt.Tooltip("region:N", title="Region"),
            alt.Tooltip("nivel:N", title="Nivel"),
            alt.Tooltip("atenciones:Q", title="Atenciones", format=","),
            alt.Tooltip("porcentaje:Q", title="% del total", format=".2f"),
        ],
    )
    .properties(height=max(240, 18 * regional["region"].nunique()))
)

st.altair_chart(barras, width="stretch")

st.subheader("Por grupo de edad y sexo")
etapa = (
    vista.groupby(["grupo_edad", "sexo", "nivel"], observed=True)["atenciones"]
    .sum()
    .reset_index()
)
etapa["grupo_edad"] = pd.Categorical(etapa["grupo_edad"], categories=grupos, ordered=True)

calor = (
    alt.Chart(etapa)
    .mark_rect()
    .encode(
        x=alt.X("grupo_edad:N", title="Grupo de edad", sort=list(grupos)),
        y=alt.Y("sexo:N", title="Sexo"),
            color=alt.Color(
                "atenciones:Q",
                title="Atenciones",
                scale=alt.Scale(range=["#EDF4FB", "#08519C"]),
            ),
        column=alt.Column("nivel:N", title="Nivel"),
        tooltip=[
            alt.Tooltip("grupo_edad:N"),
            alt.Tooltip("sexo:N"),
            alt.Tooltip("nivel:N"),
            alt.Tooltip("atenciones:Q", format=","),
        ],
    )
    .properties(width=190)
)

st.altair_chart(calor, width="stretch")

poblacion = cargar_poblacion()
if poblacion is not None:
    st.divider()
    st.subheader(f"Atenciones por 1,000 habitantes, {ANIO_TASAS}")

    avisos = [
        "El denominador es la **poblacion identificada con DNI** que reporta el "
        f"RENIEC para {ANIO_TASAS}, no la poblacion residente. Un distrito con "
        "muchos residentes no censados aparece subrepresentado, asi que la tasa "
        "tiende a verse inflada.",
        f"Es un corte de **un solo anio**. Por eso la tasa solo se calcula para "
        f"{ANIO_TASAS}: aplicarla a 2017 con poblacion de {ANIO_TASAS} seria "
        "invalido. La serie de conteos de arriba si es comparable entre anios.",
        "Con conteos, un distrito con mas habitantes registra mas atenciones "
        "aunque su cobertura sea peor. La tasa corrige eso; el conteo no.",
    ]
    for aviso in avisos:
        st.caption(f"- {aviso}")

    base = vista[vista["anio"] == ANIO_TASAS]
    if not base.empty:
        tasas = base.groupby("region", as_index=False)["atenciones"].sum()
        tasas = tasas.merge(poblacion, on="region", how="left")
        tasas = tasas[tasas["poblacion"] > 0]
        tasas["por_1000"] = tasas["atenciones"] / tasas["poblacion"] * 1000

        escala = (
            alt.Chart(tasas)
            .mark_bar(color="#2E7D6F")
            .encode(
                x=alt.X("por_1000:Q", title="Atenciones por 1,000 habitantes identificados"),
                y=alt.Y("region:N", title="Region", sort="-x"),
                tooltip=[
                    alt.Tooltip("region:N", title="Region"),
                    alt.Tooltip("atenciones:Q", title="Atenciones", format=","),
                    alt.Tooltip("poblacion:Q", title="Poblacion con DNI", format=","),
                    alt.Tooltip("por_1000:Q", title="Por 1,000", format=".1f"),
                ],
            )
            .properties(height=max(240, 18 * len(tasas)))
        )
        st.altair_chart(escala, width="stretch")

st.divider()
st.caption(
    "Cada dimension reserva la categoria *No registrado* para los registros del "
    "archivo original que llegan sin nivel de establecimiento, sin distrito o con "
    "valores fuera del catalogo. No se descartan: se muestran aparte para que el "
    "total siga cuadrando con la fuente."
)
