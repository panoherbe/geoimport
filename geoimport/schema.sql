-- Pipeline bookkeeping in its own schema (THB answers 29 and 47). The platform's tables are never altered: the
-- pipeline only replaces the rows of one source in them.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS pipeline_meta;

CREATE TABLE IF NOT EXISTS pipeline_meta.import_lauf (
    id          bigserial PRIMARY KEY,
    start       timestamptz NOT NULL DEFAULT now(),
    ende        timestamptz,
    ausloeser   text NOT NULL,
    bericht     jsonb
);

CREATE TABLE IF NOT EXISTS pipeline_meta.quelle (
    name                   text PRIMARY KEY,
    typ                    text NOT NULL,
    adresse                text NOT NULL,
    ziel_tabelle           text NOT NULL,
    letzter_lauf           bigint REFERENCES pipeline_meta.import_lauf(id),
    letzter_erfolg         timestamptz,
    letzte_roh_pruefsumme  text
);

-- checksum per object key as of the last replace: the basis for "neu, geändert, entfernt"
CREATE TABLE IF NOT EXISTS pipeline_meta.objekt_stand (
    quelle      text NOT NULL REFERENCES pipeline_meta.quelle(name),
    schluessel  text NOT NULL,
    pruefsumme  text NOT NULL,
    PRIMARY KEY (quelle, schluessel)
);

-- change log per run (THB answers 25, 30, 51: a protocol per run, no versions in the target tables)
CREATE TABLE IF NOT EXISTS pipeline_meta.aenderung (
    import_lauf bigint NOT NULL REFERENCES pipeline_meta.import_lauf(id),
    quelle      text NOT NULL,
    schluessel  text NOT NULL,
    art         text NOT NULL CHECK (art IN ('neu', 'geaendert', 'entfernt'))
);
CREATE INDEX IF NOT EXISTS aenderung_lauf_idx ON pipeline_meta.aenderung (import_lauf, quelle);
