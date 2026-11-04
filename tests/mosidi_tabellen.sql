-- Minimal target tables for the tests and the prototype stack, written for this repository. Column names follow the
-- platform MOSIDI (open-source project MOSIDI-ORG/MOSIDI); only the columns the pipeline, its configuration and its
-- tests use are defined. Table names are set per installation in sources/*.yaml (ziel.tabelle). The binding schema
-- is the platform's own.
CREATE EXTENSION IF NOT EXISTS postgis;

-- municipalities (geometry source geobasis_gemeinden)
CREATE TABLE "Kommunale Gebiete Brandenburg" (
    id NUMERIC PRIMARY KEY,
    geom geometry(MULTIPOLYGON, 4326),
    identifier VARCHAR(254),
    name VARCHAR(254),
    nationalco VARCHAR(254),
    inspireid VARCHAR(254),
    nationalle VARCHAR(254),
    national_1 VARCHAR(254),
    country VARCHAR(254),
    beginlifes VARCHAR(24),
    lowerlevel VARCHAR(254),
    upperlevel VARCHAR(254)
);

-- indicator values of several sources in one table, told apart by the column source (CSV source inkar_gemeinden)
CREATE TABLE dashboard_data_de (
    id SERIAL PRIMARY KEY,
    indikator TEXT,
    kennziffer TEXT,
    wert NUMERIC,
    zeitbezug INTEGER,
    granularity VARCHAR(50),
    source VARCHAR(200)
);

-- one DCAT-AP row per target table; the pipeline writes only the columns that exist here
CREATE TABLE table_metadata (
    table_name TEXT PRIMARY KEY,
    dct_title TEXT,
    dct_description TEXT,
    dct_catalog_publisher TEXT,
    dct_accessurl TEXT,
    dct_accrualperiodicity TEXT,
    dct_bbox geometry(GEOMETRY, -1),
    dct_modified TIMESTAMP WITHOUT TIME ZONE,
    imported TIMESTAMP WITHOUT TIME ZONE
);
