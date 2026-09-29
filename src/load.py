"""Escritura del modelo estrella en SQL Server y del snapshot en Parquet."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import config

TABLA_HECHO = "fact_atencion"
DIMENSIONES = ("dim_tiempo", "dim_territorio", "dim_nivel", "dim_grupo_edad", "dim_sexo")
DIMENSIONES_POBLACION = ("dim_poblacion", "dim_poblacion_region")
LOTE_INSERCION = 50_000


def diagnostico_conexion(exc: Exception) -> str:
    """Traduce el error opaco de FreeTDS a una causa accionable.

    pymssql devuelve el mismo codigo (20009) para "no hay nada escuchando" y
    para "no puedo autenticarme", y el mensaje no distingue ambos casos. Como el
    primero es un problema de configuracion del sistema y no del pipeline, se
    informa en vez de dejar un traceback que no dice nada util.
    """
    url = config.url_sqlserver()
    texto = str(exc)
    if "20009" not in texto and "TDS" not in texto and "Cannot open" not in texto:
        return texto

    return (
        f"No se pudo alcanzar {url.host or 'localhost'}.\n\n"
        "pymssql se conecta solo por TCP: no usa memoria compartida ni named pipes,\n"
        "aunque la instancia este funcionando. Comprueba en este orden:\n\n"
        "  1. Que la instancia exista y este encendida:\n"
        "       Get-Service 'MSSQL$SQLEXPRESS'\n"
        "  2. Que TCP/IP este habilitado y que haya un listener:\n"
        "       Get-NetTCPConnection -State Listen -LocalPort 1433\n"
        "  3. Que TCP/IP este habilitado en el registro (requiere administrador):\n"
        "       $i = 'HKLM:\\SOFTWARE\\Microsoft\\Microsoft SQL Server\\MSSQL16.SQLEXPRESS\\MSSQLServer\\SuperSocketNetLib\\Tcp\\IPAll'\n"
        "       (Get-Item $i).GetValue('Enabled')\n"
        "  4. Reinicia el servicio tras cambiar el registro:\n"
        "       Restart-Service 'MSSQL$SQLEXPRESS'\n\n"
        "Mientras tanto, el pipeline corre igual con los snapshots en Parquet:\n"
        "    python -m src.pipeline --solo-parquet"
    )


def _engine(base: str, host: str, usuario: str | None, clave: str | None) -> Engine:
    """Construye el motor con `creator` en lugar de una URL.

    El nombre de instancia lleva una barra invertida (`localhost\\SQLEXPRESS`).
    SQLAlchemy no la escapa al renderizar la URL, lo que produce una cadena que
    el parser no entiende. Con el creator, `server` viaja intacto hasta pymssql
    y el problema desaparece de raiz en vez de depender de un escapado fragil.
    """

    def _creator():
        import pymssql

        parametros: dict[str, object] = {"server": host, "database": base}
        if usuario:
            parametros["user"] = usuario
        if clave:
            parametros["password"] = clave
        return pymssql.connect(**parametros)

    return create_engine("mssql+pymssql://", creator=_creator, pool_pre_ping=True, future=True)


def motor() -> Engine:
    """Motor apuntando a la base destino del pipeline."""
    url = config.url_sqlserver()
    return _engine(url.database or "master", url.host or "localhost", url.username, url.password)


def crear_base() -> None:
    """Crea la base destino si no existe. Se conecta a master para poder hacerlo."""
    url = config.url_sqlserver()
    destino = url.database or "master"
    sentencia = f"IF DB_ID('{destino}') IS NULL CREATE DATABASE [{destino}];"
    motor_maestro = _engine("master", url.host or "localhost", url.username, url.password)
    # CREATE DATABASE no puede correr dentro de una transaccion explicita:
    # se usa AUTOCOMMIT a nivel de conexion, sin envolverlo en .begin().
    with motor_maestro.connect() as conexion:
        conexion = conexion.execution_options(isolation_level="AUTOCOMMIT")
        conexion.execute(text(sentencia))


def _sentencias(ddl: str) -> list[str]:
    """Divide un script T-SQL en sentencias ejecutables.

    No alcanza con partir por ';'. Un CREATE TABLE termina en ');' y ese punto
    y coma cae dentro del parentesis de la definicion, asi que un split ingenuo
    deja la sentencia a medias con un parentesis sin cerrar. Y un
    'IF ... BEGIN ... END;' contiene punto y comas a nivel de bloque, que si se
    parten producen un 'IF' sin su 'END'.

    Aqui se sigue la profundidad de parentesis, el anidamiento BEGIN/END y el
    estado de las comillas, y se parte solo en los ';' que de verdad cierran una
    sentencia. Las lineas de comentario se descartan porque algunos drivers las
    rechazan al enviaslas sueltas.
    """
    limpio = "\n".join(linea for linea in ddl.splitlines() if not linea.lstrip().startswith("--"))

    sentencias: list[str] = []
    actual: list[str] = []
    profundidad = 0
    bloques = 0
    en_cadena = False
    palabra = ""

    for caracter in limpio:
        actual.append(caracter)

        if en_cadena:
            en_cadena = caracter != "'"
            continue

        if caracter == "'":
            en_cadena = True
            palabra = ""
            continue

        if caracter in "()":
            profundidad += 1 if caracter == "(" else -1
            palabra = ""
            continue

        if caracter.isalpha():
            palabra += caracter.lower()
            continue

        # Un caracter no alfabetico cierra la palabra en curso: solo cuenta si
        # estamos fuera de parentesis y fuera de un literal.
        if profundidad == 0:
            if palabra == "begin":
                bloques += 1
            elif palabra == "end":
                bloques = max(0, bloques - 1)
        palabra = ""

        if caracter == ";" and profundidad == 0 and bloques == 0:
            actual.pop()
            sentencias.append("".join(actual))
            actual = []

    resto = "".join(actual).strip()
    if resto:
        sentencias.append(resto)
    return [s for s in (fragmento.strip() for fragmento in sentencias) if s]


def crear_esquema(motor_: Engine) -> None:
    ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with motor_.begin() as conexion:
        for sentencia in _sentencias(ddl):
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


def _cargar_poblacion(
    motor_: Engine, base: Path, progreso: Callable[[str], None] | None = None
) -> None:
    """Carga las tablas de poblacion desde sus Parquet, si existen.

    Se leen del Parquet y no de memoria a proposito: asi SQL Server y el Parquet
    que usa el dashboard quedan garantidamente iguales, que es la misma
    invariante que verifica `scripts/cargar_sql.py`. Si RIDA todavia no se
    descargo, se saltea sin fallar: el pipeline tiene que correr igual sin
    poblacion, solo pierde las tasas.
    """
    for nombre in DIMENSIONES_POBLACION:
        ruta = base / f"{nombre}.parquet"
        if not ruta.exists():
            if progreso is not None:
                progreso(f"      {nombre}: sin Parquet, se omite")
            continue
        if progreso is not None:
            progreso(f"      {nombre}")
        pd.read_parquet(ruta).to_sql(
            nombre, motor_, if_exists="append", index=False, chunksize=LOTE_INSERCION
        )


def _vaciar(motor_: Engine, tablas: tuple[str, ...]) -> None:
    """Limpia las tablas del modelo para una recarga idempotente.

    Se usa DELETE y no TRUNCATE porque SQL Server impide truncar una tabla
    referenciada por una clave foranea (error 1785) aunque este vacia: el
    TRUNCATE valida que la constraint exista, no que haya filas. Por eso se
    vacia primero el hecho y despues las dimensiones, y se resetea la
    identidad para que el id_atencion vuelva a empezar en 1.
    """
    with motor_.begin() as conexion:
        conexion.execute(text(f"DELETE FROM dbo.{TABLA_HECHO}"))
        for nombre in tablas:
            conexion.execute(text(f"DELETE FROM dbo.{nombre}"))
        conexion.execute(text(f"DBCC CHECKIDENT ('dbo.{TABLA_HECHO}', RESEED, 0)"))


def _insertar_hecho(
    motor_: Engine, hecho: pd.DataFrame, progreso: Callable[[str], None] | None = None
) -> int:
    """Inserta el hecho por lotes informando el avance.

    Va con executemany (el metodo por defecto de pandas) y no con
    method='multi' a proposito. 'multi' arma un unico INSERT con todos los
    valores del lote, y SQL Server rechaza un INSERT con mas de 1000
    expresiones de fila (error 10738): con 50.000 filas y 6 columnas salen
    300.000 valores y la carga revienta. Habria que bajar el lote a 166 filas
    para que entre, y medido en esta maquina eso duplica el tiempo de carga
    (22,5 min contra 10,5 min para las 2,6M filas).

    El progreso no es decorativo: son 2.6M filas en 53 lotes, y sin una linea
    por lote el proceso parece colgado durante minutos.
    """
    total = 0
    lotes = max(1, -(-len(hecho) // LOTE_INSERCION))
    for numero, inicio in enumerate(range(0, len(hecho), LOTE_INSERCION), start=1):
        trozo = hecho.iloc[inicio : inicio + LOTE_INSERCION]
        trozo.to_sql(TABLA_HECHO, motor_, if_exists="append", index=False, chunksize=LOTE_INSERCION)
        total += len(trozo)
        if progreso is not None:
            porcentaje = 100 * numero / lotes
            progreso(f"      lote {numero:>3}/{lotes}  {total:>10,} filas  ({porcentaje:5.1f}%)")
    return total


def cargar(motor_: Engine, dimensiones: dict[str, pd.DataFrame], hechos: pd.DataFrame) -> int:
    """Reconstruye dimensiones y hecho. Es idempotente: reemplaza el contenido."""
    ancho = _resolver_claves(dimensiones, hechos)
    _vaciar(motor_, DIMENSIONES + DIMENSIONES_POBLACION)

    for nombre, tabla in dimensiones.items():
        tabla.to_sql(nombre, motor_, if_exists="append", index=False, chunksize=LOTE_INSERCION)

    total = _insertar_hecho(motor_, ancho)
    _cargar_poblacion(motor_, config.PROCESSED_DIR)
    return total


def cargar_desde_parquet(
    motor_: Engine,
    directorio: Path | None = None,
    progreso: Callable[[str], None] | None = None,
) -> int:
    """Carga el modelo estrella desde los snapshots en Parquet ya generados.

    Es el atajo para iterar sobre el modelo o la capa de consulta sin repetir
    el ETL completo, que son ~35 minutos. Los snapshots ya traen las claves
    surrogate resueltas, asi que aqui solo se copian a SQL Server.

    Devuelve la cantidad de filas del hecho cargadas.
    """
    base = directorio or config.PROCESSED_DIR
    _vaciar(motor_, DIMENSIONES + DIMENSIONES_POBLACION)

    for nombre in DIMENSIONES:
        if progreso is not None:
            progreso(f"      {nombre}")
        tabla = pd.read_parquet(base / f"{nombre}.parquet")
        tabla.to_sql(nombre, motor_, if_exists="append", index=False, chunksize=LOTE_INSERCION)

    if progreso is not None:
        progreso(f"      {TABLA_HECHO}")
    hecho = pd.read_parquet(base / f"{TABLA_HECHO}.parquet")
    total = _insertar_hecho(motor_, hecho, progreso)
    _cargar_poblacion(motor_, base, progreso)
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
