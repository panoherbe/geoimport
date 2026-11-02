"""geoimport run [--source NAME] [--dry-run] [--nur-faellige] | geoimport dienst [--intervall S]

`run` is one import run over all (or one) configured sources. A failing source is recorded in the report and the
run continues with the next one. The exit code is 0 when every source succeeded and 1 otherwise, so a scheduler can
alert on it. `dienst` is the long-running Compose service: it starts a run every --intervall seconds and only
imports the sources whose rhythm (rhythmus_tage) is due.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from . import __version__
from .config import ConfigError, load_sources
from .load import write
from .sources import fetch_csv, fetch_wfs

LOCK_ID = 0x6765_6F69  # pg_advisory_lock key: one run at a time


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> psycopg.Connection:
    """DATABASE_URL, or the POSTGRES_* variables the MOSIDI Compose file already sets for its backend."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg.connect(url, autocommit=True)
    e = os.environ
    return psycopg.connect(host=e.get("POSTGRES_HOST", "brandenburg_db"), port=e.get("POSTGRES_PORT", "5432"),
                           dbname=e["POSTGRES_DB"], user=e["POSTGRES_USER"], password=e["POSTGRES_PASSWORD"],
                           autocommit=True)


def summary(b: dict) -> str:
    lines = [f"Importlauf {b['lauf_id']} · {b['start']} · {b['laufzeit_s']} s · {b['ausloeser']}"
             + (" · PROBELAUF, nichts geschrieben" if b["probelauf"] else "")]
    for q in b["quellen"]:
        if q["status"] == "erfolgreich":
            lines.append(f"  {q['name']} ({q['typ']}) → {q['ziel']}: {q['gelesen']} gelesen, {q['neu']} neu, "
                         f"{q['geaendert']} geändert, {q['geloescht']} entfernt, {q['unveraendert']} unverändert"
                         + (", ersetzt" if q["ersetzt"] else ", Zieltabelle nicht angefasst") + f" ({q['dauer_s']} s)")
        elif q["status"] == "nicht fällig":
            lines.append(f"  {q['name']}: nicht fällig (Rhythmus {q['rhythmus_tage']} Tage)")
        else:
            lines.append(f"  {q['name']} ({q['typ']}): FEHLGESCHLAGEN: {q['fehler']}")
        lines += [f"    Warnung: {w}" for w in q.get("warnungen", [])]
    return "\n".join(lines)


def faellig(conn: psycopg.Connection, name: str, tage: int) -> bool:
    if not tage:
        return True
    row = conn.execute("SELECT letzter_erfolg > now() - make_interval(days => %s) FROM pipeline_meta.quelle "
                       "WHERE name = %s", (tage, name)).fetchone()
    return not (row and row[0])


def aufraeumen(base: Path, name: str, behalten: int) -> None:
    runs = sorted((base / "data" / "raw" / name).glob("lauf-*"), key=lambda p: int(p.name.split("-")[1]))
    for old in runs[:-behalten] if behalten else []:
        shutil.rmtree(old, ignore_errors=True)


def run(args) -> int:
    base = Path(args.basis).resolve()
    try:
        sources = load_sources(base / "sources")
    except ConfigError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 2
    if args.source:
        sources = [s for s in sources if s.name == args.source]
        if not sources:
            print(f"Quelle {args.source} nicht konfiguriert", file=sys.stderr)
            return 2
    conn = connect()
    conn.execute((Path(__file__).with_name("schema.sql")).read_text())
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
        print("Ein anderer Lauf ist aktiv, Abbruch", file=sys.stderr)
        return 3
    t0, start = time.monotonic(), now()
    lauf = conn.execute("INSERT INTO pipeline_meta.import_lauf (ausloeser) VALUES (%s) RETURNING id",
                        (args.ausloeser,)).fetchone()[0]
    quellen = []
    for src in sources:
        q = {"name": src.name, "typ": src.typ, "adresse": src.url or src.pfad, "ziel": src.ziel["tabelle"],
             "zeitstempel": now()}
        if args.nur_faellige and not faellig(conn, src.name, src.rhythmus_tage):
            quellen.append({**q, "status": "nicht fällig", "rhythmus_tage": src.rhythmus_tage})
            continue
        rohdir = base / "data" / "raw" / src.name / f"lauf-{lauf}"
        t1 = time.monotonic()
        try:
            res = fetch_wfs(src, rohdir) if src.typ == "wfs" else fetch_csv(src, rohdir, base)
            z = write(conn, src, res, lauf, args.dry_run)
            q.update(status="erfolgreich", gemeldet=res.gemeldet, vollstaendig=res.vollstaendig,
                     gefiltert=res.gefiltert, gelesen=z.gelesen, neu=z.neu, geaendert=z.geaendert,
                     geloescht=z.geloescht, unveraendert=z.unveraendert, ersetzt=z.ersetzt,
                     warnungen=res.warnungen[:20] + z.warnungen, rohdaten=res.rohdaten)
        except Exception as e:  # one source failing must not stop the run
            q.update(status="fehlgeschlagen", fehler=f"{type(e).__name__}: {e}"[:500])
            if args.verbose:
                traceback.print_exc()
        q["dauer_s"] = round(time.monotonic() - t1, 1)
        aufraeumen(base, src.name, src.rohdaten_behalten)
        quellen.append(q)
    bericht = {"lauf_id": lauf, "version": __version__, "start": start, "ende": now(),
               "laufzeit_s": round(time.monotonic() - t0, 1), "ausloeser": args.ausloeser,
               "probelauf": args.dry_run, "quellen": quellen,
               "status": "erfolgreich" if all(q["status"] != "fehlgeschlagen" for q in quellen)
               else "teilweise fehlgeschlagen"}
    conn.execute("UPDATE pipeline_meta.import_lauf SET ende = now(), bericht = %s WHERE id = %s",
                 (json.dumps(bericht), lauf))
    out = base / "reports"
    out.mkdir(exist_ok=True)
    (out / f"lauf-{lauf}.json").write_text(json.dumps(bericht, ensure_ascii=False, indent=2), encoding="utf-8")
    text = summary(bericht)
    (out / f"lauf-{lauf}.txt").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    conn.close()
    return 0 if bericht["status"] == "erfolgreich" else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="geoimport")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hilfe in (("run", "Importlauf über alle oder eine Quelle"),
                        ("dienst", "Dauerdienst für Compose: prüft regelmäßig, welche Quellen fällig sind")):
        r = sub.add_parser(name, help=hilfe)
        r.add_argument("--basis", default=os.environ.get("GEOIMPORT_BASIS", "."),
                       help="Ordner mit sources/, data/, reports/")
        r.add_argument("-v", "--verbose", action="store_true")
        if name == "run":
            r.add_argument("--source", help="nur diese Quelle")
            r.add_argument("--dry-run", action="store_true", help="alles prüfen und berichten, nichts schreiben")
            r.add_argument("--nur-faellige", action="store_true", help="nur Quellen, deren Rhythmus fällig ist")
            r.add_argument("--ausloeser", default="manuell", choices=["manuell", "zeitplan", "test"])
        else:
            r.add_argument("--intervall", type=int, default=3600, help="Sekunden zwischen zwei Prüfungen")
    args = ap.parse_args()
    if args.cmd == "run":
        sys.exit(run(args))
    args.source, args.dry_run, args.nur_faellige, args.ausloeser = None, False, True, "zeitplan"
    while True:  # failures are in the report and the exit code of each run; the service keeps going
        try:
            run(args)
        except Exception:
            traceback.print_exc()
        time.sleep(args.intervall)


if __name__ == "__main__":
    main()
