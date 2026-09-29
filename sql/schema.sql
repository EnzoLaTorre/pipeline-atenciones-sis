-- Modelo estrella de atenciones del SIS.
-- El detalle original (~3.3 millones de filas por semestre) no cabe en SQL
-- Server Express (10 GB), asi que el pipeline agrega al grano de abajo durante
-- la ingesta. Cada dimension reserva la clave 0 para "no registrado", de modo
-- que ninguna fila del hecho queda huerfana aunque el dato fuente venga null.

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_tiempo')
BEGIN
    CREATE TABLE dbo.dim_tiempo (
        id_tiempo INT NOT NULL PRIMARY KEY,
        anio      INT NOT NULL,
        mes       INT NOT NULL,
        CONSTRAINT uq_dim_tiempo UNIQUE (anio, mes)
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_territorio')
BEGIN
    CREATE TABLE dbo.dim_territorio (
        id_territorio   INT NOT NULL PRIMARY KEY,
        ubigeo_distrito NVARCHAR(6) NOT NULL,
        region          NVARCHAR(100) NOT NULL,
        provincia       NVARCHAR(100) NOT NULL,
        distrito        NVARCHAR(100) NOT NULL,
        CONSTRAINT uq_dim_territorio UNIQUE (ubigeo_distrito)
    );
    CREATE INDEX ix_dim_territorio_region ON dbo.dim_territorio (region);
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_nivel')
BEGIN
    CREATE TABLE dbo.dim_nivel (
        id_nivel INT NOT NULL PRIMARY KEY,
        codigo   NVARCHAR(20) NOT NULL,
        etiqueta NVARCHAR(100) NOT NULL,
        orden    INT NOT NULL
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_grupo_edad')
BEGIN
    CREATE TABLE dbo.dim_grupo_edad (
        id_grupo_edad INT NOT NULL PRIMARY KEY,
        descripcion   NVARCHAR(100) NOT NULL,
        orden         INT NOT NULL
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_sexo')
BEGIN
    CREATE TABLE dbo.dim_sexo (
        id_sexo  INT NOT NULL PRIMARY KEY,
        codigo   NVARCHAR(20) NOT NULL,
        etiqueta NVARCHAR(50) NOT NULL
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'fact_atencion')
BEGIN
    CREATE TABLE dbo.fact_atencion (
        id_atencion   BIGINT IDENTITY(1,1) NOT NULL,
        id_tiempo     INT NOT NULL REFERENCES dbo.dim_tiempo (id_tiempo),
        id_territorio INT NOT NULL REFERENCES dbo.dim_territorio (id_territorio),
        id_nivel      INT NOT NULL REFERENCES dbo.dim_nivel (id_nivel),
        id_grupo_edad INT NOT NULL REFERENCES dbo.dim_grupo_edad (id_grupo_edad),
        id_sexo       INT NOT NULL REFERENCES dbo.dim_sexo (id_sexo),
        atenciones    BIGINT NOT NULL,
        CONSTRAINT pk_fact_atencion PRIMARY KEY CLUSTERED (id_atencion)
    );
    CREATE INDEX ix_fact_tiempo     ON dbo.fact_atencion (id_tiempo);
    CREATE INDEX ix_fact_territorio ON dbo.fact_atencion (id_territorio);
    CREATE INDEX ix_fact_nivel      ON dbo.fact_atencion (id_nivel);
END;

-- Denominador de las tasas. Es poblacion identificada con DNI segun RIDA 2025,
-- no poblacion residente: sirve para comparar entre distritos, no para medir
-- cobertura de servicios. No lleva columna de anio porque el dataset es de un
-- solo ano; si se agrega otro, la clave pasa a ser (anio, ubigeo_distrito).
--
-- No lleva clave foranea contra dim_territorio a proposito: RIDA tiene cuatro
-- distritos mas que el SIS (1,892 contra 1,889), y una FK obligaria a descartar
-- esa poblacion o a inventar un territorio que no existe. La cobertura
-- incompleta se reporta en data/reports/calidad.json en vez de ocultarse.
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_poblacion')
BEGIN
    CREATE TABLE dbo.dim_poblacion (
        ubigeo_distrito NVARCHAR(6) NOT NULL PRIMARY KEY,
        poblacion       BIGINT NOT NULL
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'dim_poblacion_region')
BEGIN
    CREATE TABLE dbo.dim_poblacion_region (
        region    NVARCHAR(100) NOT NULL PRIMARY KEY,
        poblacion BIGINT NOT NULL,
        distritos INT NOT NULL
    );
END;
