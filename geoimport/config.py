"""Source configuration: one YAML file per source in the sources directory, checked before anything is fetched."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REQUIRED = {"wfs": ["name", "typ", "url", "layer", "objekt_id", "ziel"],
            "csv": ["name", "typ", "pfad", "objekt_id", "ziel"]}
ZIEL_KEYS = {"tabelle", "schema", "quelle_spalte", "quelle_wert", "geometrie_spalte", "spalten", "feste_werte",
             "metadaten"}


class ConfigError(ValueError):
    pass


@dataclass
class Source:
    name: str
    typ: str  # "wfs" or "csv"
    objekt_id: str | list[str]  # WFS: "gml_id" or an attribute; CSV: column(s) forming the stable key
    ziel: dict  # target table in the platform's model, see README "Ziel"
    url: str = ""
    layer: str = ""
    version: str = "2.0.0"
    seitengroesse: int = 100
    zeitlimit: int = 120  # seconds per HTTP request; some WFS need minutes for a page of large polygons
    crs_quelle: int = 25833  # SRID the source delivers (WFS: requested via SRSNAME)
    pfad: str = ""  # CSV: local path or http(s) URL, also a ZIP archive
    zip_eintrag: str = "*.csv"  # file inside a ZIP archive (glob)
    trennzeichen: str = ","
    kodierung: str = "utf-8"
    geometrie: dict = field(default_factory=dict)  # CSV: {wkt: col} or {x: col, y: col}; empty = attributes only
    attribute: dict = field(default_factory=dict)  # name -> source field; empty = take all
    filter: dict = field(default_factory=dict)  # field -> value, list of values, or "prefix*"
    dezimalkomma: list = field(default_factory=list)  # fields with a decimal comma, written with a point
    max_loeschanteil: float = 0.2
    rhythmus_tage: int = 0  # 0 = every run; otherwise skipped by "run --nur-faellige" until due
    rohdaten_behalten: int = 5  # raw deliveries kept per source
    datei: str = ""

    @property
    def schluessel(self) -> list[str]:
        return [self.objekt_id] if isinstance(self.objekt_id, str) else list(self.objekt_id)

    @property
    def quelle_wert(self) -> str:
        return self.ziel.get("quelle_wert", self.name)


def load_sources(directory: Path) -> list[Source]:
    sources = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("."):  # editor backups, macOS AppleDouble files ("._name.yaml") copied by tar
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        typ = raw.get("typ")
        if typ not in REQUIRED:
            raise ConfigError(f"{path.name}: typ muss 'wfs' oder 'csv' sein, ist {typ!r}")
        missing = [k for k in REQUIRED[typ] if not raw.get(k)]
        if missing:
            raise ConfigError(f"{path.name}: Pflichtfelder fehlen: {', '.join(missing)}")
        unknown = set(raw) - set(Source.__dataclass_fields__)
        if unknown:
            raise ConfigError(f"{path.name}: unbekannte Schlüssel: {', '.join(sorted(unknown))}")
        ziel = raw["ziel"]
        if not isinstance(ziel, dict) or not ziel.get("tabelle"):
            raise ConfigError(f"{path.name}: ziel braucht 'tabelle'")
        if set(ziel) - ZIEL_KEYS:
            raise ConfigError(f"{path.name}: unbekannte Schlüssel in ziel: {', '.join(sorted(set(ziel) - ZIEL_KEYS))}")
        g = set(raw.get("geometrie", {}))
        if typ == "csv" and ziel.get("geometrie_spalte") and not ({"wkt"} <= g or {"x", "y"} <= g):
            raise ConfigError(f"{path.name}: geometrie braucht 'wkt' oder 'x' und 'y'")
        sources.append(Source(**raw, datei=path.name))
    names = [s.name for s in sources]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"Quellname doppelt: {', '.join(sorted(dupes))}")
    return sources
