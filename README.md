# geoimport (Prototyp)

Import-Pipeline für **WFS-Dienste** und **CSV-Dateien** in das bestehende Datenmodell der Plattform **MOSIDI**
(PostgreSQL/PostGIS), mit Änderungsprotokoll und Bericht je Lauf. Prototyp; Lizenz Apache 2.0.

## Was er kann

- **WFS 2.0:** Gesamtzahl per `RESULTTYPE=hits`, seitenweiser Abruf mit `STARTINDEX`/`COUNT`, wenn der Dienst
  laut GetCapabilities Paging kann (`ImplementsResultPaging`), sonst ein einziger Abruf; wiederholt ein Dienst
  Seiten, gilt der Abruf als unvollständig. Zeitlimit je Quelle, Wiederholung bei Fehlern; GML wird nach lokalen
  Elementnamen gelesen.
- **CSV:** per URL oder als abgelegte Datei, auch im ZIP (Deflate64 über `unzip -p`, etwa INKAR mit 6,6 GB), als
  Datenstrom; Filter (`Raumbezug: Gemeinden`, `Kennziffer: "12*"`), zusammengesetzte Schlüssel, Dezimalkomma.
- **Rohablage:** jede Lieferung wird vor dem Einlesen mit SHA-256 unter `data/raw/` abgelegt (die letzten fünf
  je Quelle). Eine unveränderte CSV-Lieferung wird nicht noch einmal eingelesen.
- **Zieltabellen bleiben unverändert:** Die Pipeline ersetzt nur die Zeilen ihrer Quelle (Spalte `source` oder
  die ganze Tabelle einer Geometriequelle), in einer Transaktion. Werte, die der Spaltentyp nicht annimmt, werden
  übersprungen und gemeldet. Geometrien gehen nach EPSG:4326 (bzw. in die SRID der Spalte), repariert und auf den
  Spaltentyp gebracht.
- **Änderungsprotokoll:** Abgleich je Schlüssel und Prüfsumme gegen `pipeline_meta.objekt_stand`; neu, geändert
  und entfernt je Lauf in `pipeline_meta.aenderung`. Unveränderte Quellen lassen die Zieltabelle unberührt.
- **Sicherheit:** ersetzt wird nur nach vollständigem Abruf und nie, wenn mehr als 20 % einer Quelle entfallen
  würden (einstellbar). Eine ausgefallene Quelle stoppt nur sich selbst.
- **Metadaten:** DCAT-AP-Eintrag in `table_metadata` (nur die konfigurierten Felder, dazu Zeitstempel und Ausdehnung).
- **Bericht:** `reports/lauf-<id>.json` und `.txt`, zusätzlich in `pipeline_meta.import_lauf`.
- **Dienst:** `geoimport dienst` prüft stündlich, welche Quellen fällig sind (`rhythmus_tage`), und importiert nur diese.

## Start (eigenes Compose, wie MOSIDI aufgebaut)

```bash
docker compose up -d brandenburg_db                   # postgis/postgis mit den drei MOSIDI-Tabellen
docker compose run --rm geoimport run                 # alle Quellen
docker compose run --rm geoimport run --source inkar_gemeinden --dry-run
docker compose run --rm test                          # Unit- und Ende-zu-Ende-Tests
```

Exit-Code 0 = alle Quellen erfolgreich, 1 = mindestens eine fehlgeschlagen.

## In MOSIDI einbinden

Ordner nach `services/geoimport` kopieren, dann mit der vorhandenen `.env`:

```bash
docker compose -f docker-compose.yml -f services/geoimport/docker-compose.mosidi.yml up -d geoimport
docker compose -f docker-compose.yml -f services/geoimport/docker-compose.mosidi.yml run --rm geoimport run
```

## Neue Quelle

Eine YAML-Datei in `sources/`. Beispiele: `geobasis_gemeinden.yaml` (WFS der Geobasis Brandenburg →
„Kommunale Gebiete Brandenburg (InNoWest)“) und `inkar_gemeinden.yaml` (INKAR → `dashboard_data_de`, `source =
INKAR`). Unter `ziel` stehen Tabelle, Quellenspalte, Geometriespalte, Spaltenabbildung (`@zeile` für eine laufende
Nummer, `@objekt_id` für den Schlüssel), feste Werte und Metadaten. Unbekannte oder fehlende Schlüssel meldet die
Pipeline vor dem ersten Abruf.

## Grenzen des Prototyps

Die Tabellendefinitionen stammen aus dem öffentlichen MOSIDI-Repository (`tests/mosidi_tabellen.sql`); das
verbindliche Schema stellt die THB im Auftrag bereit. Die Suche nach dem neuesten INKAR-Jahrgang und der Import der
Indikator-Beschreibungen (`dashboard_data_metadata`) sind noch nicht enthalten. Wiederholte Elemente gleichen
Namens im GML werden zusammengefasst (der letzte Wert gilt).
