-- The three MOSIDI tables the pipeline writes to, copied unchanged from MOSIDI-ORG/MOSIDI
-- (services/backend/src/db_table_schema/schema.sqlalchemy, commit 26cadbb, 2026-11-02).
-- Loaded into the prototype database so tests and the end-to-end run use the platform's real structure.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE "Kommunale Gebiete Brandenburg (InNoWest)" (
	id NUMERIC NOT NULL, 
	geom geometry(MULTIPOLYGON,4326), 
	identifier VARCHAR(254), 
	name VARCHAR(254), 
	nationalco VARCHAR(254), 
	inspireid VARCHAR(254), 
	nationalle VARCHAR(254), 
	national_1 VARCHAR(254), 
	country VARCHAR(254), 
	residenceo VARCHAR(254), 
	beginlifes VARCHAR(24), 
	endlifespa VARCHAR(24), 
	condominiu VARCHAR(254), 
	lowerlevel VARCHAR(254), 
	upperlevel VARCHAR(254), 
	administer VARCHAR(254), 
	coadminist VARCHAR(254), 
	boundary VARCHAR(254), 
	CONSTRAINT kommunales_gebiet_pkey PRIMARY KEY (id)
);

CREATE TABLE dashboard_data_de (
	id SERIAL NOT NULL, 
	indikator TEXT, 
	kennziffer TEXT, 
	wert NUMERIC, 
	zeitbezug INTEGER, 
	granularity VARCHAR(50), 
	source VARCHAR(200), 
	data_type VARCHAR(50), 
	CONSTRAINT dashboard_data_de_expanded_pkey PRIMARY KEY (id)
);

CREATE TABLE table_metadata (
	dct_title TEXT, 
	dct_description TEXT, 
	dct_catalog_title TEXT, 
	dct_catalog_description TEXT, 
	dct_catalog_publisher TEXT, 
	dct_accessurl TEXT, 
	dct_license TEXT, 
	dct_identifier TEXT, 
	dcatde_contributorid TEXT, 
	dct_distribution TEXT, 
	dct_language TEXT, 
	dct_bbox geometry(GEOMETRY,-1), 
	dct_centroid geometry(GEOMETRY,-1), 
	geometry_type TEXT, 
	dcatde_politicalgeocodingleveluri TEXT, 
	dcatde_politicalgeocodinguri TEXT, 
	dcatde_geocodingtext TEXT, 
	dct_modified TIMESTAMP WITHOUT TIME ZONE, 
	dct_issued TIMESTAMP WITHOUT TIME ZONE, 
	dct_accrualperiodicity TEXT, 
	dct_temporal_startdata TIMESTAMP WITHOUT TIME ZONE, 
	dct_temporal_enddate TIMESTAMP WITHOUT TIME ZONE, 
	table_name TEXT NOT NULL, 
	details JSONB, 
	imported TIMESTAMP WITHOUT TIME ZONE, 
	dct_type TEXT, 
	legend_url TEXT, 
	CONSTRAINT table_metadata_pkey PRIMARY KEY (table_name)
);
