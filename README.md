# Pipeline de atenciones del SIS

Pipeline ETL que convierte los archivos publicados por el Estado Peruano en un
modelo estrella en SQL Server, para analizar **en qué nivel de atención se
registran las atenciones del SIS** y cómo cambió ese reparto entre 2017 y 2025.

> **Qué se está contando.** El campo de la fuente es `ATENCIONES`, no
> `consultas`. Incluye contactos preventivos y promocionales, no solo
> atendimento clínico. Por eso la tasa nacional de 2025 sale en ~2,7 atenciones
> por persona al año, una cifra que no tiene sentido como número de consultas
> médicas. Todo el análisis usa "atenciones" con ese sentido.

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

Eso importa porque el Ministerio de Salud del Perú, en la
[RM 464-2011/MINSA](https://inen.sld.pe/portal/documentos/pdf/normas_legales/resoluciones_ministeriales/2011/02112011_RM464_2011_MINSA.pdf)
estima que el primer nivel *"podría resolverse localmente entre el 70 y el 80 % de
las necesidades básicas más frecuentes"*. Si el sistema se aleja de ese rango, es
un problema de política de salud con consecuencias en presupuesto y en tiempo de
espera de los pacientes.

> La cita importa porque el rango es fácil de deformar. La fuente peruana dice
> **70-80 %** y habla de *necesidades resueltas localmente*, no de *porcentaje de
> atenciones registradas en primer nivel*. No son la misma magnitud, y el
> dashboard lo advierte al pie del gráfico.

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
refutaron en la primera consulta: 85.1 % de las atenciones de 2021 se
resolvían en el primer nivel, dentro del rango de referencia. La pregunta
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

dim_poblacion       1,892 filas  ubigeo, población identificada RIDA 2025
dim_poblacion_region   26 filas  región, población, distritos
```

**Cada dimensión reserva la clave `0` para "no registrado".** El archivo fuente
tiene 27,216 filas sin `UBIGEO_DISTRITO` legible y 16,666 con nivel `0` o vacío.
Descartarlas rompería la cuadra con la fuente; dejarlas como `NULL` colgaría
filas huérfanas. Con un miembro desconocido explícito, cada fila del hecho
apunta a una dimensión válida y el total siempre reconcilia.

**El modelo está cargado y cuadra.** `python scripts/cargar_sql.py` crea la base,
aplica el DDL y puebla las tablas desde los Parquet; al terminar compara cada
tabla contra el Parquet de origen y cuenta las filas del hecho que no
encuentran dimensión. Resultado de la última carga: 2,604,139 filas,
665,738,377 atenciones, las 8 tablas cuadradas contra sus Parquet y **0 filas
huérfanas**. Como el hecho guarda el grano agregado y no el detalle, esa cifra
de 665,738,377 tiene que reconciliar exactamente con el total de la fuente; si
alguna vez no lo hace, el problema está en la transformación, no en la carga.

**Las tablas de población no llevan clave foránea contra `dim_territorio`, y es
deliberado.** RIDA tiene 1,892 distritos y el SIS 1,889: una FK obligaría a
descartar esa población o a inventar un territorio que no existe. La cobertura
incompleta se reporta en `data/reports/calidad.json` en vez de esconderse detrás
de una restricción.

**El análisis se puede reproducir entero en SQL, no solo en el dashboard.**
Partiendo del modelo, la tasa nacional de 2025 sale en SQL directamente:

```sql
WITH atenciones AS (
    SELECT r.region, SUM(f.atenciones) AS atenciones
    FROM   fact_atencion f
    JOIN   dim_territorio r ON r.id_territorio = f.id_territorio
    JOIN   dim_tiempo     t ON t.id_tiempo     = f.id_tiempo
    WHERE  t.anio = 2025
    GROUP BY r.region
)
SELECT 1000.0 * SUM(a.atenciones) / SUM(pr.poblacion) AS tasa_por_1000
FROM   atenciones a
JOIN   dim_poblacion_region pr ON pr.region = a.region;
```

Da 2,703.6 atenciones por cada 1,000 habitantes, el mismo valor que muestra el
dashboard. Que los dos caminos coincidan no es casualidad: `cargar_sql.py` carga
SQL desde los Parquet, no al revés.

El `GROUP BY` del CTE no es opcional. Sin él, `SUM(pr.poblacion)` suma el
denominador una vez por cada fila del hecho y la tasa sale 0,2 en vez de 2,703.6:
un error de cuatro órdenes de magnitud que no da ningún error, solo una cifra
que parece plausible si no se sabe el orden de magnitud esperado.

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

- **`UBIGEO_DISTRITO` con ceros iniciales.** El más importante, y el que más
  tiempo costó ver. El código es un identificador de seis dígitos, pero al
  convertirlo a entero `010101` se volvía `10101`, y el catálogo oficial nunca
  casaba: solo 970 de 1,888 distritos cruzaban. El síntoma era silencioso, porque
  la mitad de los distritos afectados sí cuadraban y nada daba error. La
  corrección es no castear nunca: se lee como texto y se rellena con `zfill(6)`.
  `sql/schema.sql` guarda el campo como `NVARCHAR(6)` por la misma razón, y hay
  una prueba de regresión que falla si reaparece un código de cinco dígitos.- **`UBIGEO_DISTRITO` con tipos mezclados.** Debería ser un código numérico de
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

## 6. Población: por qué contar atenciones no alcanza

Un 82 % de atenciones en primer nivel en un distrito con mucha más población que
otro no dice nada sobre cobertura. Contar sin denominador compara el tamaño de la
población y da la respuesta equivocada.

El denominador viene del [RIDA 2025 del RENIEC](https://www.datosabiertos.gob.pe/),
con **35,843,232 personas identificadas** distribuidas en 1,892 distritos. El cruce
sale limpio: **1,888 de 1,888** distritos con atenciones del SIS encuentran
denominador. Antes del arreglo de UBIGEO ese número era 970 de 1,888, así que la
cobertura que se ve en el dashboard es consecuencia directa de haber leído el
código como texto.

Quedan 12,048 personas (0,03 %) fuera del agregado regional, y vale la pena
nombrarlas porque son de dos tipos distintos:

- 3 distritos reales (`050413` Putis, `080915` Kumpirushiato, `151026` San
  Joaquín) que RIDA reporta y el SIS no tiene atenciones.
- 1 código que **no existe en el catálogo oficial** (`160405`, 3,504 personas). Es
  un defecto de la fuente, no del cruce, y se descarta en vez de inventarle un
  distrito.

Tres cosas que hay que decir sobre este denominador, porque es fácil usarlo mal:

- **No es población residente.** Es población con DNI registrada. Un distrito con
  muchos residentes no censados aparece subrepresentado, y su tasa se ve inflada.
- **Es un corte de un solo año.** Solo se calcula la tasa de 2025. Aplicar la
  población de 2025 a 2017 sería inválido, así que el dashboard calcula conteos
  para toda la serie y tasas únicamente para 2025. Una serie histórica de tasas
  exigiría las proyecciones distritales yearly del INEI, que no están en este repo.
- **La tasa tampoco lo arregla todo.** Con denominador, un distrito grande ya no
  amasa atenciones solo por tener más habitantes, que es lo que se quería evitar.

### Dos trampas del dataset de RIDA

Ninguna de las dos produce un error visible: las dos dan un resultado plausible y
 equivocado, que es la razón por la que quedaron documentadas.

- **El archivo trae dos columnas de UBIGEO y no son la misma.** `UBIGEO_RENIEC` y
  `UBIGEO_INEI` difieren en el 84 % de las filas. El pipeline une por
  `UBIGEO_INEI`, porque es la que corresponde al catálogo territorial con el que
  se comparan los distritos del SIS. Cruzar por la de RENIEC habría unido
  distritos diferentes sin quejarse.
- **El UBIGEO ausente es un espacio en blanco, no un vacío.** Aplicar el mismo
  `zfill(6)` que arregla los ceros iniciales convertía el espacio en `000000`, un
  código con forma de distrito real. Concentró 1.3 millones de personas en un
  código fantasma, que solo se detectó porque `000000` apareció en el top 10 de
  habitantes. Los blancos se separan antes de rellenar y se reportan aparte.

## 7. Cómo ejecutarlo

Requiere Python 3.12 y SQL Server 2022 Express en la instancia `SQLEXPRESS`.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

cp .env.example .env          # ajustar credenciales si hace falta

bash scripts/download_data.sh              # baja los 14 archivos (~1.6 GB)
bash scripts/download_data.sh 2021_01_06   # o solo un periodo
bash scripts/download_poblacion.sh        # RIDA 2025 + catalogo UBIGEO (~138 MB)

python -m src.pipeline                     # ETL completo (~35 min)
python -m src.pipeline --solo-parquet      # sin tocar SQL Server
python -m src.pipeline --limite 1          # prueba rápida con un archivo

python scripts/cargar_sql.py               # puebla SQL Server desde los Parquet (~11 min)
python scripts/regenerar_resumen.py        # rehace el Parquet de la demo

streamlit run dashboards/app.py            # dashboard en http://localhost:8501

python -m pytest tests/ -q                 # 46 pruebas
```

### Publicar el dashboard

El dashboard lee solo tres Parquet versionados (`resumen_regional`,
`dim_poblacion` y `dim_poblacion_region`), así que corre en Streamlit Community
Cloud sin base de datos y sin los 1,6 GB de la fuente. En
[share.streamlit.io](https://share.streamlit.io), conectado con la cuenta de
GitHub:

| Ajuste | Valor |
|---|---|
| Repository | `EnzoLaTorre/pipeline-atenciones-sis` |
| Branch | `main` |
| Main file path | `dashboards/app.py` |
| Python version | 3.12 |

El `Main file path` es el paso que se olvida: la app no está en la raíz del
repositorio, y sin ponerlo Streamlit busca `streamlit_app.py` y falla.

Las descargas se hacen contra el catálogo CKAN, no con URLs escritas a mano,
porque el patrón de los nombres cambió en 2024: hasta 2023 terminan en `_0.zip`,
desde 2024 no. Ambas piden `User-Agent` de navegador porque el WAF del portal
devuelve HTTP 418 a clientes sin identificar. `download_poblacion.sh` resuelve
sus URLs desde el índice del catálogo y saltea lo que ya está en `data/raw/`.

## 8. Estructura

```
scripts/download_data.sh      descarga desde el catálogo CKAN
scripts/download_poblacion.sh descarga RIDA 2025 y catálogo UBIGEO
scripts/regenerar_resumen.py  rehace el Parquet sin reprocesar 1.6 GB
scripts/cargar_sql.py         crea la base y puebla SQL Server desde los Parquet
src/config.py                 rutas, catálogo de columnas, mapeo canónico
src/extract.py                lectura en lotes desde el ZIP
src/transform.py              limpieza, tipado, UBIGEO, agregado, dimensiones
src/poblacion.py              ingesta RIDA, auditoría de cobertura, agregado regional
src/load.py                   modelo estrella en SQL Server + Parquet
src/pipeline.py               orquestador
dashboards/app.py             dashboard Streamlit sobre Parquet
sql/schema.sql                DDL del modelo estrella
tests/                        46 pruebas sobre fixtures con datos sucios
data/reports/calidad.json     informe de calidad de cada corrida
```

## 9. Alcance y límites

Este es un proyecto individual de portafolio. Lo que **no** tiene, y conviene
decir de entrada:

- **No hay incrementalidad.** Cada corrida reprocesa los 14 archivos completos
  en unos 35 minutos. Con 9 años que es aceptable; con 50 años, no.
- **No hay orquestación ni reintentos.** Se ejecuta a mano y falla ruidosamente.
- **La capa SQL no tiene tests de integración automatizados.** Las 46 pruebas
  cubren la transformación, el cruce con población, el armado del DDL y el
  diagnóstico de conexión. Que la carga escriba las cifras correctas se
  comprueba con `scripts/cargar_sql.py`, que compara cada tabla contra los
  Parquet y busca filas huérfanas, pero se ejecuta a mano.
- **Las tasas solo existen para 2025.** El denominador disponible es población
  identificada con DNI de un solo año. Comparar por población entre 2017 y 2025
  exigiría una serie anual que este proyecto no trae. Es el límite más visible
  del análisis y está expuesto en el dashboard, no escondido.
- **Sin vista por prestación.** Se descartó la columna de servicio CIE para
  mantener el pipeline rápido. Es la extensión más natural.

## Configurar SQL Server

`pymssql` se conecta **solo por TCP**: no usa memoria compartida ni named pipes,
aunque la instancia esté perfectamente sana. En una instalación normal esto no
hay que tocarlo, pero en esta máquina hizo falta, y el síntoma era confuso: el
registro decía que TCP/IP estaba habilitado y aun así no había ningún puerto en
escucha.

La causa era `TcpPort` vacío junto con `TcpDynamicPorts = 0`: el motor no
lograba determinar un puerto de escucha y no abría ninguno. La corrección es
fijar el puerto y reiniciar el servicio, en consola **como administrador**:

```powershell
$ipall = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\MSSQL16.SQLEXPRESS\MSSQLServer\SuperSocketNetLib\Tcp\IPAll'
Set-ItemProperty $ipall -Name TcpPort -Value '1433' -Type String
Set-ItemProperty $ipall -Name TcpDynamicPorts -Value '' -Type String
Restart-Service 'MSSQL$SQLEXPRESS'
Get-NetTCPConnection -State Listen -LocalPort 1433
```

Si el último comando no devuelve nada, `python -m src.pipeline` lo dice con un
diagnóstico que apunta a la causa en vez de devolver el error 20009 de FreeTDS,
que no distingue "no hay nada escuchando" de "no puedo autenticarme".

Con el puerto abierto, `python scripts/cargar_sql.py` crea la base, aplica el
DDL y puebla el modelo desde los Parquet, verificando cada tabla al terminar.
Tarda unos 11 minutos para 2,6 millones de filas.

Vale la pena registrar lo que salió mal en el camino, porque son las cuatro cosas
que más se rompen al escribir un cargador contra SQL Server:

- **La URL de instancia con barra invertida.** `mssql+pymssql://localhost\SQLEXPRESS`
  no escapa el `\`, y el parser de SQLAlchemy no entiende lo que recibe. Se
  resuelve con `creator=`, que pasa el nombre de instancia a `pymssql` intacto.
- **Partir el DDL por `;`.** Un `CREATE TABLE` termina en `);`, y ese punto y coma
  cae dentro del paréntesis: un `split` ingenuo por `;` deja la sentencia con un
  paréntesis sin cerrar y SQL Server responde `Incorrect syntax near )`. Los
  `IF ... BEGIN ... END` tienen el mismo problema. `src/load.py` sigue la
  profundidad de paréntesis y el anidamiento `BEGIN/END`.
- **`TRUNCATE` sobre tablas con clave foránea.** SQL Server lo prohíbe aunque la
  tabla esté vacía (error 1785), porque valida que la constraint exista. La
  recarga usa `DELETE` más `DBCC CHECKIDENT`.
- **`method="multi"` con lotes grandes.** Genera un `INSERT` con todos los
  valores del lote; con 50.000 filas y 6 columnas son 300.000 expresiones de fila
  y se supera el máximo de 1000 (error 10738). Va con `executemany`, que además
  medido en esta máquina tarda la mitad.

Mientras tanto, **el pipeline funciona igual sin SQL Server**: `python -m
src.pipeline --solo-parquet` escribe los snapshots en Parquet que alimentan el
dashboard en línea. Ese es el camino que se versiona y el que se puede ver sin
instalar nada.

## Licencia

Código: MIT. Datos: ODC-By, según la fuente oficial. Los ZIP de `data/raw/` no
se versionan; se descargan con el script.
