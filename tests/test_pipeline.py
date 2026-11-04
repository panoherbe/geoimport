"""Unit tests (no database) and an end-to-end test against PostGIS with MOSIDI's tables, a local fake WFS and a
zipped CSV shaped like INKAR.

The end-to-end test needs DATABASE_URL pointing at a PostGIS database it may wipe (docker compose run test).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from geoimport.config import ConfigError, Source, load_sources
from geoimport.load import plan
from geoimport.sources import FetchResult, checksum, fetch_csv, fetch_wfs, matches, parse_wfs_page

POLY = "{x} 5800000 {x1} 5800000 {x1} 5800100 {x} 5800000"
MOSIDI_SQL = Path(__file__).with_name("mosidi_tabellen.sql")
GEBIETE = "Kommunale Gebiete Brandenburg"


def gml(features: list[tuple[str, str]], matched: int | str, start: int, count: int) -> bytes:
    page = features[start:start + count]
    members = "".join(
        f'<wfs:member><app:Gebiet gml:id="{oid}"><app:name>{name}</app:name><app:ags>12{i:06d}</app:ags>'
        f'<app:ebene>{"Bundesland" if oid == "land" else "Kommunales Gebiet"}</app:ebene>'
        f'<app:geom><gml:Polygon gml:id="g_{oid}" srsName="urn:ogc:def:crs:EPSG::25833"><gml:exterior><gml:LinearRing>'
        f'<gml:posList>{POLY.format(x=400000 + i * 200, x1=400100 + i * 200)}</gml:posList>'
        f'</gml:LinearRing></gml:exterior></gml:Polygon></app:geom></app:Gebiet></wfs:member>'
        for i, (oid, name) in enumerate(page, start=start))
    return (f'<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            f'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:app="urn:test" numberMatched="{matched}" '
            f'numberReturned="{len(page)}">{members}</wfs:FeatureCollection>').encode()


class FakeWFS(BaseHTTPRequestHandler):
    features: list[tuple[str, str]] = []
    matched: int | str | None = None  # override numberMatched to simulate an incomplete answer
    paging: str | None = "TRUE"  # ImplementsResultPaging in GetCapabilities; None = constraint missing
    honours_startindex = True  # False: like the LGB service, every page starts at 0

    def do_GET(self):  # noqa: N802
        q = {k.upper(): v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        matched = self.matched if self.matched is not None else len(self.features)
        if q.get("REQUEST", "").upper() == "GETCAPABILITIES":
            constraint = ("" if self.paging is None else f'<ows:Constraint name="ImplementsResultPaging"><ows:NoValues/>'
                          f'<ows:DefaultValue>{self.paging}</ows:DefaultValue></ows:Constraint>')
            body = (f'<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" '
                    f'xmlns:ows="http://www.opengis.net/ows/1.1"><ows:OperationsMetadata>{constraint}'
                    f'</ows:OperationsMetadata></wfs:WFS_Capabilities>').encode()
        elif q.get("RESULTTYPE") == "hits":
            body = gml([], matched, 0, 0)
        else:
            start = int(q.get("STARTINDEX", 0)) if self.honours_startindex else 0
            body = gml(self.features, matched, start, int(q.get("COUNT", 1000)))
        self.send_response(200)
        self.send_header("Content-Type", "application/gml+xml")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


INKAR_HEADER = "Bereich;ID;Kuerzel;Indikator;Raumbezug;Kennziffer;Name;Zeitbezug;Wert"


def inkar_zip(path: Path, rows: list[str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("inkar_2025.csv", "\n".join([INKAR_HEADER, *rows]) + "\n")
        z.writestr("Readme.txt", "test")


# --- unit tests -------------------------------------------------------------------------------------------------

def wfs_src(**kw) -> Source:
    return Source(name="t", typ="wfs", objekt_id="gml_id", ziel={"tabelle": GEBIETE}, url="u", layer="l", **kw)


def test_parse_page_dispatches_on_local_names():
    feats, matched, returned, warn = parse_wfs_page(gml([("a", "Eins"), ("b", "Zwei")], 2, 0, 10), wfs_src())
    assert (matched, returned, warn) == (2, 2, [])
    assert [f.objekt_id for f in feats] == ["a", "b"]
    assert feats[0].attribute["name"] == "Eins"
    assert "srsName" not in feats[0].geom


def test_checksum_ignores_gml_ids():
    a = checksum({"n": 1}, '<gml:Polygon gml:id="x1"><gml:posList>1 2</gml:posList></gml:Polygon>')
    b = checksum({"n": 1}, '<gml:Polygon gml:id="x2"><gml:posList>1  2</gml:posList></gml:Polygon>')
    assert a == b and a != checksum({"n": 2}, '<gml:Polygon><gml:posList>1 2</gml:posList></gml:Polygon>')


def test_filter_values_lists_and_prefixes():
    assert matches({"r": "Gemeinden", "k": "12051000"}, {"r": "Gemeinden", "k": "12*"})
    assert not matches({"r": "Gemeinden", "k": "11000000"}, {"k": "12*"})
    assert matches({"r": "Kreise"}, {"r": ["Gemeinden", "Kreise"]})


def test_plan_replaces_only_after_complete_fetch_and_within_delete_share():
    src = wfs_src()
    res = FetchResult([], True, 5, [], [])
    assert plan(5, 0, 1, res, src) is None  # 1 of 5 = 20 %, allowed
    assert "nicht ersetzt" in plan(5, 0, 2, res, src)  # 40 % would vanish
    assert "unvollständig" in plan(5, 0, 0, FetchResult([], False, 5, [], []), src)


def test_config_rejects_typos_and_missing_target(tmp_path):
    (tmp_path / "a.yaml").write_text("name: a\ntyp: wfs\nurl: u\nlayer: l\nobjekt_id: gml_id\nziel: {tabelle: x}\n"
                                     "seitengrosse: 5\n")
    with pytest.raises(ConfigError, match="unbekannte Schlüssel: seitengrosse"):
        load_sources(tmp_path)
    (tmp_path / "a.yaml").write_text("name: a\ntyp: wfs\nurl: u\nlayer: l\nobjekt_id: gml_id\n")
    with pytest.raises(ConfigError, match="Pflichtfelder fehlen: ziel"):
        load_sources(tmp_path)
    (tmp_path / "a.yaml").write_text("name: a\ntyp: wfs\nurl: u\nlayer: l\nobjekt_id: gml_id\nziel: {tabel: x}\n")
    with pytest.raises(ConfigError, match="ziel braucht 'tabelle'"):
        load_sources(tmp_path)


def test_csv_in_zip_streams_with_filter_composite_key_and_decimal_comma(tmp_path):
    inkar_zip(tmp_path / "inkar.zip", ["Bevölkerung;1;bev;Einwohner;Gemeinden;12051000;Brandenburg;2023;72.124,5",
                                       "Bevölkerung;1;bev;Einwohner;Kreise;12051;Brandenburg;2023;72.124,5",
                                       "Bevölkerung;1;bev;Einwohner;Gemeinden;11000000;Berlin;2023;3.755.251"])
    src = Source(name="i", typ="csv", objekt_id=["Kennziffer", "Kuerzel", "Zeitbezug"], ziel={"tabelle": "d"},
                 pfad="inkar.zip", zip_eintrag="inkar_*.csv", trennzeichen=";", dezimalkomma=["Wert"],
                 filter={"Raumbezug": "Gemeinden", "Kennziffer": "12*"})
    res = fetch_csv(src, tmp_path / "roh", tmp_path)
    feats = list(res.features)
    assert [f.objekt_id for f in feats] == ["12051000|bev|2023"] and feats[0].attribute["Wert"] == "72124.5"
    assert (res.gemeldet, res.gefiltert) == (3, 2) and res.roh_pruefsumme
    assert (tmp_path / "roh" / "inkar.zip").exists()  # the delivery is archived before it is read


# --- WFS behaviour against a local fake service -----------------------------------------------------------------

@pytest.fixture
def wfs():
    server = HTTPServer(("127.0.0.1", 0), FakeWFS)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeWFS.features, FakeWFS.matched = [("land", "Brandenburg"), ("a", "Eins"), ("b", "Zwei"), ("c", "Drei")], None
    FakeWFS.paging, FakeWFS.honours_startindex = "TRUE", True
    yield f"http://127.0.0.1:{server.server_port}/wfs"
    server.shutdown()


def test_wfs_without_paging_is_read_in_one_request(tmp_path, wfs):
    FakeWFS.paging, FakeWFS.honours_startindex = "FALSE", False  # the LGB service, measured 2026-11-02
    src = Source(name="t", typ="wfs", objekt_id="gml_id", ziel={"tabelle": GEBIETE}, url=wfs, layer="app:Gebiet",
                 seitengroesse=2, filter={"ebene": "Kommunales Gebiet"})
    res = fetch_wfs(src, tmp_path)
    assert sorted(f.objekt_id for f in res.features) == ["a", "b", "c"] and res.vollstaendig and res.gefiltert == 1
    assert len(res.rohdaten) == 1 and "ImplementsResultPaging=FALSE" in res.warnungen[0]


def test_wfs_that_ignores_startindex_stops_and_is_incomplete(tmp_path, wfs):
    FakeWFS.paging, FakeWFS.honours_startindex = None, False  # no constraint declared, offset ignored
    src = Source(name="t", typ="wfs", objekt_id="gml_id", ziel={"tabelle": GEBIETE}, url=wfs, layer="app:Gebiet",
                 seitengroesse=2)
    res = fetch_wfs(src, tmp_path)
    assert len(res.features) == 2 and not res.vollstaendig  # never claims completeness, so nothing is replaced
    assert any("ignoriert STARTINDEX" in w for w in res.warnungen)


# --- end-to-end against PostGIS with MOSIDI's tables -------------------------------------------------------------

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


def run(basis: Path, *extra: str) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, "-m", "geoimport", "run", "--basis", str(basis), "--ausloeser", "test", *extra],
                       capture_output=True, text=True)
    latest = max((basis / "reports").glob("lauf-*.json"), key=lambda f: int(f.stem.split("-")[1]))
    return p.returncode, {q["name"]: q for q in json.loads(latest.read_text())["quellen"]}


def counts(q):
    return q["neu"], q["geaendert"], q["geloescht"], q["unveraendert"]


@needs_db
def test_end_to_end_into_mosidi_tables(tmp_path, wfs):
    import psycopg
    db = os.environ["DATABASE_URL"]
    with psycopg.connect(db, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS pipeline_meta CASCADE; DROP TABLE IF EXISTS "{GEBIETE}", dashboard_data_de, '
                  "table_metadata")
        c.execute(MOSIDI_SQL.read_text())
        c.execute("INSERT INTO dashboard_data_de (indikator, kennziffer, wert, zeitbezug, granularity, source) "
                  "VALUES ('kitas', '12051000', 31, 2024, 'Gemeindeebene', 'OpenStreetMap (OSM)')")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "gebiete.yaml").write_text(
        f"name: gebiete\ntyp: wfs\nurl: {wfs}\nlayer: app:Gebiet\nseitengroesse: 2\nobjekt_id: gml_id\n"
        "max_loeschanteil: 0.5\nfilter: {ebene: Kommunales Gebiet}\n"
        f"ziel:\n  tabelle: {GEBIETE}\n  geometrie_spalte: geom\n"
        "  spalten: {id: '@zeile', identifier: '@objekt_id', name: name, nationalco: ags}\n"
        "  metadaten: {dct_title: Kommunale Gebiete, dct_catalog_publisher: LGB}\n")
    (tmp_path / "sources" / "inkar.yaml").write_text(
        "name: inkar\ntyp: csv\npfad: inkar.zip\nzip_eintrag: 'inkar_*.csv'\ntrennzeichen: ';'\n"
        "objekt_id: [Kennziffer, Bereich, Kuerzel, Indikator, Zeitbezug]\nfilter: {Raumbezug: Gemeinden}\n"
        "dezimalkomma: [Wert]\nmax_loeschanteil: 0.5\n"
        "ziel:\n  tabelle: dashboard_data_de\n  quelle_spalte: source\n  quelle_wert: INKAR\n"
        "  spalten: {indikator: Kuerzel, kennziffer: Kennziffer, wert: Wert, zeitbezug: Zeitbezug}\n"
        "  feste_werte: {granularity: Gemeindeebene}\n")
    rows = ["B;1;bev;Einwohner;Gemeinden;12051000;Brandenburg;2023;72.124",
            "B;1;bev;Einwohner;Gemeinden;12052000;Cottbus;2023;98.693",
            "B;2;alo;Arbeitslose;Gemeinden;12051000;Brandenburg;2023;6,4",
            "B;3;wan;Wanderung;Gemeinden;12051000;Brandenburg;2019-2023;1,2"]  # range: no integer for zeitbezug
    inkar_zip(tmp_path / "inkar.zip", rows)

    code, r = run(tmp_path, "--dry-run")  # 0: dry run writes nothing
    with psycopg.connect(db) as c:
        assert c.execute(f'SELECT count(*) FROM "{GEBIETE}"').fetchone()[0] == 0

    code, r = run(tmp_path)  # 1: everything new; paging over 2 pages; the year range is skipped and reported
    assert code == 0 and counts(r["gebiete"]) == (3, 0, 0, 0) and counts(r["inkar"]) == (3, 0, 0, 0)
    assert r["gebiete"]["gefiltert"] == 1 and len(r["gebiete"]["rohdaten"]) == 2
    assert any("zeitbezug" in w and "2019-2023" in w for w in r["inkar"]["warnungen"])

    code, r = run(tmp_path)  # 2: idempotent; the unchanged file is not even read again
    assert counts(r["gebiete"]) == (0, 0, 0, 3) and not r["gebiete"]["ersetzt"]
    assert counts(r["inkar"]) == (0, 0, 0, 3) and "nicht neu eingelesen" in r["inkar"]["warnungen"][0]

    FakeWFS.features = [("land", "Brandenburg"), ("a", "Eins"), ("b", "Zwei geändert")]  # 3: change + removal
    inkar_zip(tmp_path / "inkar.zip", rows[:1] + ["B;1;bev;Einwohner;Gemeinden;12052000;Cottbus;2023;98.700"] + rows[3:])
    code, r = run(tmp_path)
    assert counts(r["gebiete"]) == (0, 1, 1, 1) and counts(r["inkar"]) == (0, 1, 1, 1)

    FakeWFS.features, FakeWFS.matched = [("a", "Eins")], 3  # 4: incomplete answer, the source is not replaced
    code, r = run(tmp_path)
    assert counts(r["gebiete"]) == (0, 0, 0, 2) and not r["gebiete"]["ersetzt"]
    assert any("unvollständig" in w for w in r["gebiete"]["warnungen"])

    (tmp_path / "sources" / "kaputt.yaml").write_text(  # 5: a dead source fails alone
        "name: kaputt\ntyp: wfs\nurl: http://127.0.0.1:9/wfs\nlayer: x\nobjekt_id: gml_id\nziel: {tabelle: x}\n")
    code, r = run(tmp_path)
    assert code == 1 and r["kaputt"]["status"] == "fehlgeschlagen" and r["inkar"]["status"] == "erfolgreich"

    with psycopg.connect(db) as c:
        assert c.execute(f'SELECT name, nationalco FROM "{GEBIETE}" ORDER BY id').fetchall() == [
            ("Eins", "12000001"), ("Zwei geändert", "12000002")]
        typ, srid, x = c.execute(f'SELECT GeometryType(geom), ST_SRID(geom), ST_X(ST_Centroid(geom)) FROM "{GEBIETE}" '
                                 "LIMIT 1").fetchone()
        assert (typ, srid) == ("MULTIPOLYGON", 4326) and 13 < x < 14  # UTM 33N polygon now in WGS84 degrees
        assert c.execute("SELECT kennziffer, wert, zeitbezug, granularity FROM dashboard_data_de WHERE source = 'INKAR' "
                         "ORDER BY kennziffer").fetchall() == [("12051000", 72124, 2023, "Gemeindeebene"),
                                                                ("12052000", 98700, 2023, "Gemeindeebene")]
        assert c.execute("SELECT count(*) FROM dashboard_data_de WHERE source = 'OpenStreetMap (OSM)'").fetchone()[0] == 1
        assert c.execute("SELECT dct_title, dct_bbox IS NOT NULL FROM table_metadata WHERE table_name = %s",
                         (GEBIETE,)).fetchone() == ("Kommunale Gebiete", True)
        log = c.execute("SELECT quelle, art, count(*) FROM pipeline_meta.aenderung GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        assert log == [("gebiete", "entfernt", 1), ("gebiete", "geaendert", 1), ("gebiete", "neu", 3),
                       ("inkar", "entfernt", 1), ("inkar", "geaendert", 1), ("inkar", "neu", 3)]
