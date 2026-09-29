# Pipeline de atenciones del SIS

Pipeline ETL que convierte los archivos publicados por el Estado Peruano en un
modelo estrella en SQL Server, para analizar **en qué nivel de atención se
resuelven las consultas al SIS** y cómo cambió ese reparto entre 2017 y 2025.

- **Fuente:** [Datos Abiertos del Estado - Atenciones realizadas a los asegurados (SIS)](https://www.datosabiertos.gob.pe/dataset/datos-de-atenciones-realizadas-los-asegurados-sis) (ODC-By)
- **Volumen procesado:** 14 archivos, 1.6 GB comprimido, 74,581,049 filas crudas
- **Salida:** 665,738,377 atenciones agregadas en 2,604,139 filas de hecho
- **Stack:** Python 3.12, pandas, SQL Server 2022 Express, SQLAlchemy, Streamlit, Altair, pytest

---

## 1. El problema

El SIS reporta sus atenciones mezclando nivel de establecimiento, territorio,
servicio, sexo y grupo etario en una sola tabla plana. Cada registro trae el
detalle de un establecimiento, y no hay forma de responder preguntas como
*"¿el SIS se resuelve en el primer nivel o en los hospitales?"* sin volver a
recorrer los 1.6 GB cada vez.

Eso importa porque el principio de salud pública Peruano y la evidencia
internacional apuntan a que entre 60 % y 70 % de las consultas deberían
puderse resolver en el primer nivel de atención, sin necesidad de un hospital.
Si el sistema se aleja de ese rango, es un problema de política de salud con
consecuencias en presupuesto y en tiempo de espera de los pacientes.

## 2. El hallazgo

Consultas resueltas por nivel, sobre el total de atenciones de cada año:

| Nivel | 2017 | 2019 | 2020 | 2021 | 2023 | 2025 | Cambio 2017→2025 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Primer nivel | 82.4 % | 80.5 % | 83.6 % | 85.1 % | 80.8 % | 79.8 % | **−2.6 pp** |
| Segundo nivel | 11.4 % | 12.5 % | 10.3 % | 9.3 % | 12.0 % | 11.9 % | +0.4 pp |
| Tercer nivel | 6.1 % | 6.9 % | 6.1 % | 5.5 % | 7.2 % | 8.3 % | **+2.2 pp** |

**El reparto se movió hacia los hospitales.** En volumen absoluto el tercer nivel
creció de 4.4M a 8.1M atenciones (+84 %), bastante más rápido que el primer nivel
(59.4M a 77.3M, +30 %). El sistema se aleja del principio de resolver primero en
el primer nivel justo cuando más se expandió.

Dos advertencias que el gráfico hace explícitas:

- **El pico de 2021 es un artefacto, no una mejora.** Durante la pandemia el
  primer nivel subió a 85.1 % porque los hospitales restringieron atención
  ambulatoria. Tomar 2021 como año de referencia da la conclusión equivocada.
- **2020 no es comparable.** Las atenciones cayeron de 63.3M a 33.2M en un año.
  Cualquier indicador que no controle ese colapso está mezclando el cambio
  estructural con el choque externo.

### La hipótesis original era falsa

Mi primera hipótesis fue que el SIS usaba hospitales de más. Los datos la
refutaron en la primera consulta: 86.6 % de las atenciones de 2021 se
resolvían en el primer nivel, por encima del rango de referencia. La pregunta
que quedó, y que sí resultó informativa, es la evolución de esa proporción.
Queda registrado aquí porque es parte del resultado: la hipótesis original
era incorrecta y el dato la descartó.

## 3. Arquitectura

Tres capas, porque el detalle no cabe donde se quiere consultar.

```
data/raw/         ZIP del Estado (1.6 GB, ignorado por Git)
      |
      |  lectura en lotes de 250,000 filas, sin descomprimir a disco
      v
  staging         pandas, en memoria, un lote a la vez
      |
      |  agregado al grano del modelo estrella
      v
SQL Server        modelo estrella (2.6M filas)  +  Parquet agregado
```

**Por qué no se carga el detalle en SQL Server.** SQL Server Express tiene un
límite de 10 GB por base de datos. Las 74.6M filas del detalle, con sus columnas
de texto largo, no entran. En vez de recortar el alcance para que quepa, el
pipeline agrega al grano durante la ingesta: se procesa todo el detalle, pero
solo se persiste lo que responde la pregunta. La reducción es de 74.6M a 2.6M
filas y la información no se pierde, porque las columnas que no están en el grano
se agregan dentro de él.

**Por qué dos salidas.** SQL Server es el modelo consultable, con dimensiones
normalizadas y claves foráneas. El Parquet (`resumen_regional.parquet`, 268 KB)
es el agregado desnormalizado que alimenta la demo en línea, para que funcione
sin base de datos y sin costear nada.

**Por qué se descartan 7 de las 17 columnas.** `COD_UNIDAD_EJECUTORA`,
`DESC_UNIDAD_EJECUTORA`, `COD_IPRESS`, `IPRESS`, `PLAN_SEGURO`, `COD_SERVICIO`
y `DESC_SERVICIO` no participan del grano. Leerlas y normalizarlas cuesta tiempo
y memoria sin efecto en el resultado. Están declaradas en
`config.COLUMNAS_NECESARIAS` con el motivo, y ampliar el grano para una vista por
prestación o por establecimiento es agregar ahí.

## 4. Modelo de datos

Esquema estrella, en `sql/schema.sql`.

```
dim_tiempo       108 filas      año, mes
dim_territorio  1,889 filas     ubigeo, región, provincia, distrito
dim_nivel          4 filas      I, II, III, No registrado
dim_grupo_edad     7 filas      seis etapas de vida, más No catalogado
dim_sexo           3 filas      femenino, masculino, no registrado

fact_atencion  2,604,139 filas  id_tiempo, id_territorio, id_nivel,
                                id_grupo_edad, id_sexo, atenciones
```

**Cada dimensión reserva la clave `0` para "no registrado".** El archivo fuente
tiene 27,216 filas sin `UBIGEO_DISTRITO` legible y 16,666 con nivel `0` o vacío.
Descartarlas rompería la cuadra con la fuente; dejarlas como `NULL` colgaría
filas huérfanas. Con un miembro desconocido explícito, cada fila del hecho
apunta a una dimensión válida y el total siempre reconcilia.

## 5. Calidad de datos

Recuento sobre las 74,581,049 filas procesadas, publicado en
`data/reports/calidad.json` en cada corrida.

| Incidencia | Filas | % del total |
|---|---:|---:|
| `UBIGEO_DISTRITO` ilegible o ausente | 27,216 | 0.036 % |
| `NIVEL_EESS` fuera de catálogo (`0` o vacío) | 16,666 | 0.022 % |
| `SEXO` fuera de catálogo | 0 | 0 % |
| `GRUPO_EDAD` fuera de catálogo | 0 | 0 % |
| `ATENCIONES` no numérica, nula o negativa | 0 | 0 % |
| `ATENCIONES` negativas | 0 | 0 % |

Problemas reales encontrados y cómo se manejan:

- **`UBIGEO_DISTRITO` con tipos mezclados.** Debería ser un código numérico de
  seis dígitos, pero el archivo trae valores no numéricos, y de hecho
  concatenado cuando viene vacío. Se lee como texto, se convierte con
  `errors="coerce"` y lo que no convierte va al miembro desconocido.
- **Nivel `0`.** El archivo usa `0` como marcador de "sin nivel", que no es un
  valor válido del catálogo. Se trata como dato ausente, no como nivel.
- **Codificación.** El CSV declara UTF-8 y lo es. `AÑO` se normaliza a `ANIO`
  para evitar acentos en T-SQL, con un mapeo explícito de nombres canónicos en
  `config.py` en vez de normalizar a ciegas: `Ñ` se descompone en `N` + tilde
  combinante, así que el nombre crudo se convierte en `ANO`, no en `ANIO`.
- **Los 14 archivos tienen esquema idéntico** (17 columnas), verificado. Si el
  Estado publica un cambio, el pipeline lanza un error explícito en vez de
  fallar en silencio más adelante.

## 6. Cómo ejecutarlo

Requiere Python 3.12 y SQL Server 2022 Express en la instancia `SQLEXPRESS`.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

cp .env.example .env          # ajustar credenciales si hace falta

bash scripts/download_data.sh              # baja los 14 archivos (~1.6 GB)
bash scripts/download_data.sh 2021_01_06   # o solo un periodo

python -m src.pipeline                     # ETL completo (~35 min)
python -m src.pipeline --solo-parquet      # sin tocar SQL Server
python -m src.pipeline --limite 1          # prueba rápida con un archivo

python scripts/regenerar_resumen.py        # rehace el Parquet de la demo

streamlit run dashboards/app.py            # dashboard en http://localhost:8501

python -m pytest tests/ -q                 # 19 pruebas
```

La descarga se hace contra el catálogo CKAN, no con URLs escritas a mano,
porque el patrón de los nombres cambió en 2024: hasta 2023 terminan en `_0.zip`,
desde 2024 no. El script pide `User-Agent` de navegador porque el WAF del
portal devuelve HTTP 418 a clientes sin identificar.

## 7. Estructura

```
scripts/download_data.sh      descarga desde el catálogo CKAN
scripts/regenerar_resumen.py  rehace el Parquet sin reprocesar 1.6 GB
src/config.py                 rutas, catálogo de columnas, mapeo canónico
src/extract.py                lectura en lotes desde el ZIP
src/transform.py              limpieza, tipado, agregado, dimensiones
src/load.py                   modelo estrella en SQL Server + Parquet
src/pipeline.py               orquestador
dashboards/app.py             dashboard Streamlit sobre Parquet
sql/schema.sql                DDL del modelo estrella
tests/                        19 pruebas sobre fixture con datos sucios
data/reports/calidad.json     informe de calidad de cada corrida
```

## 8. Alcance y límites

Este es un proyecto individual de portafolio. Lo que **no** tiene, y conviene
decir de entrada:

- **La carga a SQL Server no está verificada en ejecución.** El modelo, el DDL y
  el cargador están escritos, pero la instancia local de SQL Server Express
  tenía el protocolo TCP/IP deshabilitado y el servicio SQL Server Browser
  detenido, y el entorno no tenía permisos de administrador para cambiarlo.
  `pymssql` solo conecta por TCP, así que la carga falla en la conexión, no en
  el código. Ver [Limitación conocida](#limitación-conocida-sql-server).
- **No hay incrementalidad.** Cada corrida reprocesa los 14 archivos completos
  en unos 35 minutos. Con 9 años que es aceptable; con 50 años, no.
- **No hay orquestación ni reintentos.** Se ejecuta a mano y falla ruidosamente.
- **Sin tests de integración contra SQL Server.** Las 19 pruebas cubren la
  transformación, que es donde está la lógica; la capa de persistencia se
  valida a mano.
- **El análisis no corrige por población.** Un 82 % de atenciones en primer nivel
  en un distrito con mucha más población que otro no significa mejor cobertura.
  Falta unir con población del INEI para comparar tasas y no solo conteos.
- **Sin vista por prestación.** Se descartó la columna de servicio CIE para
  mantener el pipeline rápido. Es la extensión más natural.

## Limitación conocida: SQL Server

Para que Python alcance la instancia hay que habilitar TCP/IP y arrancar el
servicio Browser. Es un paso del sistema, no del proyecto, y requiere consola de
administrador:

```powershell
$instancia = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\MSSQL16.SQLEXPRESS\MSSQLServer\SuperSocketNetLib\Tcp'
Set-ItemProperty "$instancia\Enabled" -Name Enabled -Value 1
Set-ItemProperty "$instancia\IpAll"  -Name Enabled -Value 1
Start-Service SQLBrowser
Set-Service SQLBrowser -StartupType Automatic
```

Después, `python -m src.pipeline` carga el modelo. La conexión usa SQLAlchemy,
así que migrar a `pyodbc` con Microsoft ODBC Driver 18 es cambiar el `drivername`
en `src/config.py`.

## Licencia

Código: MIT. Datos: ODC-By, según la fuente oficial. Los ZIP de `data/raw/` no
se versionan; se descargan con el script.
