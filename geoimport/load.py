"""Replace one source's rows in its target table, in one transaction, and log what changed.

The target tables belong to the platform and keep their structure (a requirement of the target platform): the pipeline deletes
the rows of one source and inserts the new delivery. New, changed and removed objects are computed in SQL against
pipeline_meta.objekt_stand, so the target tables need no history columns and a delivery of millions of rows never
has to fit into memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from .config import Source
from .sources import FetchResult


@dataclass
class Zaehler:
    gelesen: int = 0
    neu: int = 0
    geaendert: int = 0
    geloescht: int = 0
    unveraendert: int = 0
    ersetzt: bool = False
    warnungen: list[str] = field(default_factory=list)


def plan(n_bestand: int, neu: int, weg: int, res: FetchResult, src: Source) -> str | None:
    """Return why the source must not be replaced, or None. Replaced only after a complete fetch, and not when more
    than max_loeschanteil of its objects would vanish (a half-answering service must not empty the table)."""
    if not res.vollstaendig:
        return "Abruf unvollständig: Quelle nicht ersetzt, der bisherige Bestand bleibt"
    if weg and n_bestand and weg / n_bestand > src.max_loeschanteil:
        return (f"{weg} von {n_bestand} Objekten würden entfallen (> {src.max_loeschanteil:.0%}): "
                "Quelle nicht ersetzt, bitte Quelle prüfen")
    return None


def geom_expr(src: Source, gtyp: str, srid: int) -> sql.Composable:
    """Parse, reproject, repair and coerce to the column's geometry type (MULTIPOLYGON columns get ST_Multi)."""
    parse = "ST_GeomFromGML(geom, %s)" if src.typ == "wfs" else "ST_GeomFromText(geom, %s)"
    g = f"ST_MakeValid(ST_Transform({parse}, {srid}))" % src.crs_quelle
    base = gtyp.upper().removeprefix("MULTI")
    if base in ("POLYGON", "LINESTRING", "POINT"):
        g = f"ST_CollectionExtract({g}, {dict(POINT=1, LINESTRING=2, POLYGON=3)[base]})"
    if gtyp.upper().startswith("MULTI"):
        g = f"ST_Multi({g})"
    return sql.SQL(g)


def write(conn: psycopg.Connection, src: Source, res: FetchResult, lauf: int, dry_run: bool) -> Zaehler:
    z = Zaehler()
    ziel = src.ziel
    tab = sql.Identifier(ziel.get("schema", "public"), ziel["tabelle"])
    qsp = ziel.get("quelle_spalte")
    nur_quelle = sql.SQL(" WHERE {} = {}").format(sql.Identifier(qsp), sql.Literal(src.quelle_wert)) if qsp else sql.SQL("")
    with conn.transaction(force_rollback=dry_run):
        conn.execute("INSERT INTO pipeline_meta.quelle (name, typ, adresse, ziel_tabelle) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT (name) DO UPDATE SET typ = EXCLUDED.typ, adresse = EXCLUDED.adresse, "
                     "ziel_tabelle = EXCLUDED.ziel_tabelle", (src.name, src.typ, src.url or src.pfad, ziel["tabelle"]))
        n_bestand, roh_alt = conn.execute(
            "SELECT (SELECT count(*) FROM pipeline_meta.objekt_stand WHERE quelle = %s), letzte_roh_pruefsumme "
            "FROM pipeline_meta.quelle WHERE name = %s", (src.name, src.name)).fetchone()
        n_ziel = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(tab) + nur_quelle).fetchone()[0]
        if res.roh_pruefsumme and res.roh_pruefsumme == roh_alt and n_bestand and n_ziel == n_bestand:
            z.gelesen = z.unveraendert = n_bestand
            z.warnungen.append("Lieferung unverändert (gleiche SHA-256 wie im letzten Lauf): nicht neu eingelesen")
            conn.execute("UPDATE pipeline_meta.quelle SET letzter_lauf = %s, letzter_erfolg = now() WHERE name = %s",
                         (lauf, src.name))
            return z

        spalten = typen(conn, tab)
        mapping = dict(ziel.get("spalten", {}))
        feste = dict(ziel.get("feste_werte", {}))
        if qsp:
            feste[qsp] = src.quelle_wert
        unbekannt = (set(mapping) | set(feste) | {ziel.get("geometrie_spalte")} - {None}) - set(spalten)
        if unbekannt:
            raise RuntimeError(f"Spalten fehlen in {ziel['tabelle']}: {', '.join(sorted(unbekannt))}")
        felder = [c for c, q in mapping.items() if not q.startswith("@")]
        alle = [sql.Identifier(c) for c in ("schluessel", "pruefsumme", "geom", *felder)]
        conn.execute(sql.SQL("CREATE TEMP TABLE _neu ({}) ON COMMIT DROP").format(
            sql.SQL(", ").join(sql.SQL("{} text").format(c) for c in alle)))
        with conn.cursor() as cur, cur.copy(sql.SQL("COPY _neu ({}) FROM STDIN").format(sql.SQL(", ").join(alle))) as cp:
            for f in res.features:
                z.gelesen += 1
                cp.write_row([f.objekt_id, f.pruefsumme, f.geom or None]
                             + [None if (v := f.attribute.get(mapping[c])) in (None, "") else v for c in felder])

        dup = conn.execute("SELECT count(*) - count(DISTINCT schluessel) FROM _neu").fetchone()[0]
        if dup:
            z.warnungen.append(f"{dup} doppelte Schlüssel in der Lieferung, jeweils die letzte Zeile genommen")
            conn.execute("DELETE FROM _neu a USING _neu b WHERE a.schluessel = b.schluessel AND a.ctid < b.ctid")
        for c in felder:  # values the column type cannot take are skipped and reported, not fatal
            if spalten[c] != "text":
                bad = conn.execute(sql.SQL("DELETE FROM _neu WHERE {c} IS NOT NULL AND NOT pg_input_is_valid({c}, {t}) "
                                           "RETURNING schluessel, {c}").format(c=sql.Identifier(c), t=sql.Literal(spalten[c]))).fetchall()
                if bad:
                    z.warnungen.append(f"{len(bad)} Zeilen mit ungültigem Wert für {c} ({spalten[c]}) übersprungen, "
                                       f"z. B. {bad[0][1]!r} bei {bad[0][0]}")
        conn.execute("CREATE INDEX ON _neu (schluessel)")
        neu, geaendert, weg = conn.execute(
            "SELECT count(*) FILTER (WHERE s.schluessel IS NULL), "
            "       count(*) FILTER (WHERE s.pruefsumme <> n.pruefsumme), "
            "       (SELECT count(*) FROM pipeline_meta.objekt_stand o WHERE o.quelle = %(q)s "
            "        AND NOT EXISTS (SELECT 1 FROM _neu x WHERE x.schluessel = o.schluessel)) "
            "FROM _neu n LEFT JOIN pipeline_meta.objekt_stand s ON s.quelle = %(q)s AND s.schluessel = n.schluessel",
            {"q": src.name}).fetchone()
        n_neu = conn.execute("SELECT count(*) FROM _neu").fetchone()[0]
        grund = plan(n_bestand, neu, weg, res, src)
        if grund:
            z.warnungen.append(grund)
            z.unveraendert = n_bestand
            return z
        z.neu, z.geaendert, z.geloescht, z.unveraendert = neu, geaendert, weg, n_neu - neu - geaendert
        if neu or geaendert or weg or n_ziel != n_neu:  # unchanged deliveries leave the platform's rows alone
            ersetzen(conn, src, tab, nur_quelle, mapping, feste, felder, spalten)
            z.ersetzt = True
            conn.execute("INSERT INTO pipeline_meta.aenderung (import_lauf, quelle, schluessel, art) "
                         "SELECT %(l)s, %(q)s, n.schluessel, CASE WHEN s.schluessel IS NULL THEN 'neu' ELSE 'geaendert' END "
                         "FROM _neu n LEFT JOIN pipeline_meta.objekt_stand s ON s.quelle = %(q)s AND s.schluessel = n.schluessel "
                         "WHERE s.schluessel IS NULL OR s.pruefsumme <> n.pruefsumme "
                         "UNION ALL SELECT %(l)s, %(q)s, o.schluessel, 'entfernt' FROM pipeline_meta.objekt_stand o "
                         "WHERE o.quelle = %(q)s AND NOT EXISTS (SELECT 1 FROM _neu x WHERE x.schluessel = o.schluessel)",
                         {"l": lauf, "q": src.name})
            conn.execute("DELETE FROM pipeline_meta.objekt_stand WHERE quelle = %s", (src.name,))
            conn.execute("INSERT INTO pipeline_meta.objekt_stand (quelle, schluessel, pruefsumme) "
                         "SELECT %s, schluessel, pruefsumme FROM _neu", (src.name,))
            if ziel.get("metadaten"):
                metadaten(conn, src, tab, nur_quelle)
        conn.execute("UPDATE pipeline_meta.quelle SET letzter_lauf = %s, letzter_erfolg = now(), "
                     "letzte_roh_pruefsumme = %s WHERE name = %s", (lauf, res.roh_pruefsumme or None, src.name))
    return z


def typen(conn: psycopg.Connection, tab: sql.Identifier) -> dict[str, str]:
    rows = conn.execute("SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
                        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
                        (tab.as_string(conn),)).fetchall()
    return dict(rows)


def ersetzen(conn, src: Source, tab, nur_quelle, mapping: dict, feste: dict, felder: list, spalten: dict) -> None:
    ziel, cols, exprs = src.ziel, [], []
    for c, q in mapping.items():
        cols.append(c)
        if q == "@zeile":  # for id columns without a default, e.g. "Kommunale Gebiete" (numeric, no sequence)
            exprs.append(sql.SQL("(row_number() OVER (ORDER BY schluessel))::{}").format(sql.SQL(spalten[c])))
        elif q == "@objekt_id":
            exprs.append(sql.SQL("schluessel::{}").format(sql.SQL(spalten[c])))
        else:
            exprs.append(sql.SQL("{}::{}").format(sql.Identifier(c), sql.SQL(spalten[c])))
    for c, v in feste.items():
        cols.append(c)
        exprs.append(sql.SQL("{}::{}").format(sql.Literal(v), sql.SQL(spalten[c])))
    if ziel.get("geometrie_spalte"):
        gc = ziel["geometrie_spalte"]
        gtyp, srid = conn.execute("SELECT type, srid FROM geometry_columns WHERE f_table_schema = %s "
                                  "AND f_table_name = %s AND f_geometry_column = %s",
                                  (ziel.get("schema", "public"), ziel["tabelle"], gc)).fetchone()
        cols.append(gc)
        exprs.append(geom_expr(src, gtyp, srid or 4326))
    conn.execute(sql.SQL("DELETE FROM {}").format(tab) + nur_quelle)
    conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM _neu").format(
        tab, sql.SQL(", ").join(map(sql.Identifier, cols)), sql.SQL(", ").join(exprs)))


def metadaten(conn, src: Source, tab, nur_quelle) -> None:
    """DCAT-AP row in the platform's metadata table (MOSIDI: table_metadata, key table_name); only the fields the
    configuration names are touched, so values other scripts maintain stay as they are."""
    m = dict(src.ziel["metadaten"])
    mtab = m.pop("tabelle", "table_metadata")
    vorhanden = typen(conn, sql.Identifier("public", mtab))
    werte = {"table_name": src.ziel["tabelle"], "dct_accessurl": src.url or src.pfad, **m}
    werte = {k: v for k, v in werte.items() if k in vorhanden}
    zeit = [c for c in ("dct_modified", "imported") if c in vorhanden]
    cols = list(werte) + zeit
    vals = [sql.Literal(v) for v in werte.values()] + [sql.SQL("now()")] * len(zeit)
    upd = [c for c in cols if c != "table_name"]
    conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (table_name) DO UPDATE SET {}").format(
        sql.Identifier(mtab), sql.SQL(", ").join(map(sql.Identifier, cols)), sql.SQL(", ").join(vals),
        sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in upd)))
    gc = src.ziel.get("geometrie_spalte")
    if gc and "dct_bbox" in vorhanden:
        conn.execute(sql.SQL("UPDATE {m} SET dct_bbox = (SELECT ST_Envelope(ST_Collect({g})) FROM {t}{w}) "
                             "WHERE table_name = {n}").format(m=sql.Identifier(mtab), g=sql.Identifier(gc), t=tab,
                                                              w=nur_quelle, n=sql.Literal(src.ziel["tabelle"])))
