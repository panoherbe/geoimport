"""Fetch and parse WFS and CSV sources into Feature records.

Every response is written to the raw archive before it is parsed. CSV sources are read as a stream (INKAR is a
6.6 GB file inside a Deflate64 ZIP, which Python's zipfile cannot open, so `unzip -p` does the unpacking). GML is dispatched on local names, never on
prefixed tag strings, because prefixes differ between services (and sometimes within one). Geometries are handed
to PostGIS as GML or WKT text; PostGIS does the parsing, so no GDAL is needed in the container.
"""

from __future__ import annotations

import csv
import fnmatch
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import time
import zipfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import Source

GML_GEOMETRIES = {"Point", "MultiPoint", "LineString", "MultiCurve", "MultiLineString", "Polygon",
                  "MultiSurface", "MultiPolygon", "Surface", "Curve"}
GML_NS = "http://www.opengis.net/gml/3.2"
WFS_NS = "http://www.opengis.net/wfs/2.0"
ET.register_namespace("gml", GML_NS)


@dataclass
class Feature:
    objekt_id: str
    geom: str  # GML fragment or WKT; "" for attribute-only sources
    geom_typ: str  # "gml", "wkt" or ""
    attribute: dict
    pruefsumme: str


@dataclass
class FetchResult:
    features: Iterable[Feature]  # a list for WFS, a generator for CSV (consumed once, by the loader)
    vollstaendig: bool  # all objects the source reported were received
    gemeldet: int | None  # numberMatched, or CSV rows read (known after the generator is consumed)
    rohdaten: list[str]  # "sha256:<hex> <path>" per stored response
    warnungen: list[str]
    roh_pruefsumme: str = ""  # CSV: SHA-256 of the delivered file; unchanged file = nothing to read
    gefiltert: int = 0
    statistik: dict = field(default_factory=dict)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def checksum(attribute: dict, geom: str) -> str:
    # gml:id values and srsName spellings can change between deliveries without the object changing
    norm = re.sub(r'\s(gml:id|srsName|srsDimension)="[^"]*"', "", geom)
    norm = re.sub(r"\s+", " ", norm).strip()
    blob = json.dumps(attribute, sort_keys=True, ensure_ascii=False) + "|" + norm
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(url: str, versuche: int = 3, timeout: int = 120) -> bytes:
    for n in range(versuche):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "geoimport/0.2"}),
                                        timeout=timeout) as r:
                return r.read()
        except Exception:
            if n == versuche - 1:
                raise
            time.sleep(2 ** (n + 1))
    raise AssertionError("unreachable")


def matches(values: dict, flt: dict) -> bool:
    """filter: value, list of values, or "prefix*"."""
    for k, want in flt.items():
        v = values.get(k) or ""
        options = want if isinstance(want, list) else [want]
        if not any(v.startswith(str(o)[:-1]) if str(o).endswith("*") else v == str(o) for o in options):
            return False
    return True


def store_raw(rohdir: Path, name: str, data: bytes) -> str:
    rohdir.mkdir(parents=True, exist_ok=True)
    path = rohdir / f"{name}.gz"
    with gzip.open(path, "wb") as f:
        f.write(data)
    return f"sha256:{hashlib.sha256(data).hexdigest()} {path}"


def wfs_url(src: Source, **params) -> str:
    base = {"SERVICE": "WFS", "VERSION": src.version, "REQUEST": "GetFeature", "TYPENAMES": src.layer,
            "SRSNAME": f"urn:ogc:def:crs:EPSG::{src.crs_quelle}"}
    base.update(params)
    sep = "&" if "?" in src.url else "?"
    return src.url + sep + urllib.parse.urlencode(base)


def flatten(elem: ET.Element, prefix: str = "") -> dict:
    """Simple properties as text, nested properties as dotted names; geometry subtrees are skipped."""
    out: dict = {}
    for child in elem:
        name = prefix + local(child.tag)
        if any(local(e.tag) in GML_GEOMETRIES for e in child.iter()):
            continue
        href = child.get("{http://www.w3.org/1999/xlink}href")
        if len(child):
            out.update(flatten(child, name + "."))
        elif child.text and child.text.strip():
            out[name] = child.text.strip()
        elif href:
            out[name] = href
    return out


def parse_wfs_page(data: bytes, src: Source) -> tuple[list[Feature], int | None, int, list[str]]:
    root = ET.fromstring(data)
    if local(root.tag) == "ExceptionReport":
        text = " ".join(t.strip() for t in root.itertext() if t.strip())
        raise RuntimeError(f"WFS-Fehler: {text[:300]}")
    matched = root.get("numberMatched")
    returned = int(root.get("numberReturned", "0"))
    feats, warn = [], []
    for member in root:
        if local(member.tag) not in ("member", "featureMember"):
            continue
        for obj in member:
            geom_el = next((e for e in obj.iter() if local(e.tag) in GML_GEOMETRIES), None)
            if geom_el is None:
                warn.append(f"Objekt ohne Geometrie übersprungen: {obj.get(f'{{{GML_NS}}}id')}")
                continue
            attrs = flatten(obj)
            oid = obj.get(f"{{{GML_NS}}}id") if src.objekt_id == "gml_id" else attrs.get(src.objekt_id)
            if not oid:
                warn.append(f"Objekt ohne ID ({src.objekt_id}) übersprungen")
                continue
            if src.attribute:
                attrs = {ziel: attrs.get(quelle) for ziel, quelle in src.attribute.items()}
            gml = ET.tostring(geom_el, encoding="unicode")
            gml = re.sub(r'\ssrsName="[^"]*"', "", gml)  # SRID is passed explicitly to PostGIS
            feats.append(Feature(oid, gml, "gml", attrs, checksum(attrs, gml)))
    return feats, (int(matched) if matched and matched.isdigit() else None), returned, warn


def supports_paging(src: Source) -> bool:
    """WFS 2.0 servers declare paging in GetCapabilities; without the constraint we try and rely on the repeat guard."""
    if not src.version.startswith("2"):
        return False
    caps = ET.fromstring(get(wfs_url(src, REQUEST="GetCapabilities"), timeout=src.zeitlimit))
    for c in caps.iter():
        if local(c.tag) == "Constraint" and c.get("name") == "ImplementsResultPaging":
            value = next((e.text for e in c.iter() if local(e.tag) == "DefaultValue"), "") or ""
            return value.strip().upper() == "TRUE"
    return True


def fetch_wfs(src: Source, rohdir: Path) -> FetchResult:
    hits = ET.fromstring(get(wfs_url(src, RESULTTYPE="hits"), timeout=src.zeitlimit))
    gemeldet = hits.get("numberMatched")
    gemeldet = int(gemeldet) if gemeldet and gemeldet.isdigit() else None
    by_id: dict[str, Feature] = {}
    raw, warn, start, seite = [], [], 0, 1
    paging = supports_paging(src)
    if not paging:
        warn.append("Dienst unterstützt kein seitenweises Abrufen (ImplementsResultPaging=FALSE): ein Abruf ohne Seiten")
    while True:
        params = {"COUNT": src.seitengroesse, "STARTINDEX": start} if paging else {}
        data = get(wfs_url(src, **params), timeout=src.zeitlimit)
        raw.append(store_raw(rohdir, f"seite-{seite:04d}.xml", data))
        feats, _, returned, w = parse_wfs_page(data, src)
        warn += w
        before = len(by_id)
        by_id.update((f.objekt_id, f) for f in feats)
        start += returned
        seite += 1
        if paging and returned and len(by_id) == before:  # the service ignores STARTINDEX and repeats a page
            warn.append(f"Seite {seite - 1} bringt nur bekannte Objekte: Dienst ignoriert STARTINDEX, Abruf abgebrochen")
            break
        if not paging or returned < src.seitengroesse or (gemeldet is not None and start >= gemeldet):
            break
    if start > len(by_id) and paging:
        warn.append(f"{start - len(by_id)} doppelte Objekt-IDs, jeweils das letzte genommen")
    features = list(by_id.values())
    vollstaendig = gemeldet is not None and len(features) >= gemeldet
    if gemeldet is None:
        warn.append("Dienst meldet keine Gesamtzahl; Vollständigkeit nicht prüfbar, Quelle wird nicht ersetzt")
    elif not vollstaendig:
        warn.append(f"Abruf unvollständig: {len(features)} von {gemeldet} Objekten; Quelle wird nicht ersetzt")
    behalten = [f for f in features if matches(f.attribute, src.filter)]  # completeness counts before the filter
    return FetchResult(behalten, vollstaendig, gemeldet, raw, warn, gefiltert=len(features) - len(behalten))


def download(url: str, ziel: Path, timeout: int) -> str:
    """Stream a delivery to the raw archive (large files never sit in memory) and return its SHA-256."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "geoimport/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(ziel, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    return h.hexdigest()


def open_text(path: Path, src: Source) -> io.TextIOBase:
    """Plain CSV, or the CSV inside a ZIP; Deflate64 entries (method 9) go through `unzip -p`."""
    if not zipfile.is_zipfile(path):
        return open(path, encoding=src.kodierung, newline="")
    with zipfile.ZipFile(path) as z:
        names = [i for i in z.infolist() if fnmatch.fnmatch(i.filename, src.zip_eintrag)]
        if len(names) != 1:
            raise RuntimeError(f"ZIP enthält {len(names)} Dateien zu {src.zip_eintrag!r}, erwartet genau eine")
        entry = names[0]
    if entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        return io.TextIOWrapper(zipfile.ZipFile(path).open(entry), encoding=src.kodierung, newline="")
    if not shutil.which("unzip"):
        raise RuntimeError(f"{entry.filename}: Kompressionsverfahren {entry.compress_type} braucht das Programm unzip")
    proc = subprocess.Popen(["unzip", "-p", str(path), entry.filename], stdout=subprocess.PIPE)
    return io.TextIOWrapper(proc.stdout, encoding=src.kodierung, newline="")


def fetch_csv(src: Source, rohdir: Path, basis: Path) -> FetchResult:
    name = Path(urllib.parse.urlparse(src.pfad).path).name or "lieferung.csv"
    if src.pfad.startswith(("http://", "https://")):
        roh = rohdir / name
        digest = download(src.pfad, roh, src.zeitlimit)
    else:  # a file dropped by hand (LB 4.1), copied into the archive like a download
        rohdir.mkdir(parents=True, exist_ok=True)
        roh = rohdir / name
        shutil.copyfile(basis / src.pfad, roh)
        digest = hashlib.sha256(roh.read_bytes()).hexdigest()
    res = FetchResult([], True, None, [f"sha256:{digest} {roh}"], [], roh_pruefsumme=digest)
    res.features = _csv_rows(src, roh, res)
    return res


def _csv_rows(src: Source, path: Path, res: FetchResult) -> Iterator[Feature]:
    with open_text(path, src) as fh:
        reader = csv.reader(fh, delimiter=src.trennzeichen)
        header = next(reader)
        col = {h: i for i, h in enumerate(header)}
        g = src.geometrie
        needed = set(src.schluessel) | set(g.values()) | set(src.attribute.values()) | set(src.filter)
        missing = needed - set(col)
        if missing:
            raise RuntimeError(f"Spalten fehlen in der CSV: {', '.join(sorted(missing))}")
        flt = [(col[k], v if isinstance(v, list) else [v]) for k, v in src.filter.items()]
        keys = [col[k] for k in src.schluessel]
        felder = src.attribute or {h: h for h in header if h not in g.values()}
        n = gefiltert = 0
        for row in reader:
            n += 1
            if len(row) != len(header):
                res.warnungen.append(f"Zeile {n + 1}: {len(row)} statt {len(header)} Felder, übersprungen")
                continue
            if flt and not all(any(row[i].startswith(str(o)[:-1]) if str(o).endswith("*") else row[i] == str(o)
                                   for o in opts) for i, opts in flt):
                gefiltert += 1
                continue
            attrs = {ziel: row[col[q]] for ziel, q in felder.items()}
            for k in src.dezimalkomma:
                if attrs.get(k):
                    attrs[k] = attrs[k].replace(".", "").replace(",", ".")
            geom, typ = "", ""
            if "wkt" in g:
                geom, typ = row[col[g["wkt"]]].strip(), "wkt"
            elif g:
                try:
                    x, y = (float(row[col[g[a]]].replace(",", ".")) for a in ("x", "y"))
                except ValueError:
                    res.warnungen.append(f"Zeile {n + 1}: Koordinaten nicht lesbar, übersprungen")
                    continue
                geom, typ = f"POINT({x} {y})", "wkt"
            yield Feature("|".join(row[i] for i in keys), geom, typ, attrs, checksum(attrs, geom))
        res.gemeldet, res.gefiltert = n, gefiltert
