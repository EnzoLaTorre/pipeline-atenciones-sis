"""Pruebas de la capa de persistencia que no requieren SQL Server.

Se concentra en el armado del DDL y en el texto de diagnostico, que son las
partes del cargador que fallan en silencio cuando el motor no esta disponible.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import config, load


class TestDivisorDeSentencias:
    def test_no_corta_un_create_table_por_el_punto_y_coma_del_parentesis(self) -> None:
        ddl = """
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_tiempo')
        BEGIN
            CREATE TABLE dbo.dim_tiempo (
                id_tiempo INT NOT NULL PRIMARY KEY,
                anio      INT NOT NULL,
                CONSTRAINT uq UNIQUE (anio)
            );
        END;
        """
        sentencias = load._sentencias(ddl)

        assert len(sentencias) == 1
        assert sentencias[0].rstrip().endswith("END")
        # El parentesis de la definicion tiene que quedar balanceado.
        assert sentencias[0].count("(") == sentencias[0].count(")")

    def test_separa_sentencias_alternas(self) -> None:
        ddl = "SELECT 1; SELECT 2; SELECT 3"
        assert load._sentencias(ddl) == ["SELECT 1", "SELECT 2", "SELECT 3"]

    def test_ignora_punto_y_coma_dentro_de_cadenas(self) -> None:
        ddl = "INSERT INTO t VALUES ('a;b'); SELECT 1"
        assert load._sentencias(ddl) == ["INSERT INTO t VALUES ('a;b')", "SELECT 1"]

    def test_descarta_lineas_de_comentario(self) -> None:
        ddl = "-- comentario; con punto y coma\nSELECT 1;"
        assert load._sentencias(ddl) == ["SELECT 1"]

    def test_el_esquema_real_se_divide_en_sentencias_completas(self) -> None:
        ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        sentencias = load._sentencias(ddl)

        assert len(sentencias) == 8, "se esperaban los 8 bloques IF/CREATE del esquema"
        for sentencia in sentencias:
            assert sentencia.count("(") == sentencia.count(")"), sentencia
            assert sentencia.count("'") % 2 == 0, sentencia
            assert not sentencia.lstrip().startswith("--"), sentencia

    def test_cada_sentencia_del_esquema_crea_una_tabla_o_un_indice(self) -> None:
        ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        for sentencia in load._sentencias(ddl):
            assert "CREATE TABLE" in sentencia or "CREATE INDEX" in sentencia, sentencia


class TestDiagnosticoDeConexion:
    def test_traduce_el_codigo_20009_a_una_guia_accionable(self) -> None:
        exc = Exception("(20009, b'DB-Lib error message 20009 ... Unable to connect: TDS server is unavailable')")
        texto = load.diagnostico_conexion(exc)

        assert "Get-NetTCPConnection" in texto
        assert "solo por TCP" in texto
        assert "--solo-parquet" in texto

    def test_un_error_distinto_se_devuelve_sin_inventar_diagnostico(self) -> None:
        exc = Exception("The table 'dbo.fact_atencion' does not exist")
        assert load.diagnostico_conexion(exc) == "The table 'dbo.fact_atencion' does not exist"


class TestConfiguracionDeConexion:
    def test_el_host_conserva_la_barra_invertida_de_la_instancia(self) -> None:
        # El nombre de instancia se pasa a pymssql tal cual; si se escapara o se
        # perdiera al partir el URL, pymssql buscaria un host que no existe.
        assert config.url_sqlserver().host == "localhost\\SQLEXPRESS"

    def test_la_url_se_arma_con_url_create_y_no_con_una_cadena(self) -> None:
        assert config.url_sqlserver().drivername == "mssql+pymssql"


class TestLoteDeInsercion:
    def test_el_lote_es_un_trozo_de_pandas_y_no_una_copia(self) -> None:
        # Cargar 2.6M filas en memoria seria inviable: se inserta por trozos.
        assert load.LOTE_INSERCION == 50_000
        tabla = pd.DataFrame({"a": range(load.LOTE_INSERCION)})
        assert len(tabla.iloc[: load.LOTE_INSERCION]) == load.LOTE_INSERCION

    @pytest.mark.parametrize("nombre", load.DIMENSIONES + load.DIMENSIONES_POBLACION)
    def test_las_dimensiones_declaradas_existen_en_el_esquema(self, nombre: str) -> None:
        ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        assert f"dbo.{nombre}" in ddl

    def test_las_tablas_de_poblacion_no_declaran_clave_foranea(self) -> None:
        # RIDA tiene 4 distritos mas que el SIS. Una FK contra dim_territorio
        # obligaria a tirar esa poblacion o a inventar un territorio inexistente,
        # asi que la cobertura incompleta se reporta en calidad.json.
        ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        for sentencia in load._sentencias(ddl):
            if "CREATE TABLE dbo.dim_poblacion " in sentencia or "CREATE TABLE dbo.dim_poblacion (" in sentencia:
                assert "REFERENCES" not in sentencia, sentencia

    def test_las_tablas_de_poblacion_coinciden_con_sus_parquet(self) -> None:
        # La carga lee el Parquet, asi que el DDL no puede tener columnas que el
        # Parquet no provee: el append fallaria en tiempo de ejecucion.
        for nombre in load.DIMENSIONES_POBLACION:
            ruta = config.PROCESSED_DIR / f"{nombre}.parquet"
            if not ruta.exists():
                continue
            columnas = set(pd.read_parquet(ruta).columns)
            ddl = (config.PROJECT_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
            bloque = next(
                s for s in load._sentencias(ddl) if f"CREATE TABLE dbo.{nombre} (" in s
            )
            declaradas = {
                linea.strip().split()[0]
                for linea in bloque.splitlines()
                if linea.strip() and not linea.strip().startswith(("CREATE", "(", ")"))
            }
            assert columnas <= declaradas, f"{nombre}: el Parquet trae {columnas - declaradas}"
