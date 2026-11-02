# Testprotokoll geoimport 0.2, 02.11.2026

Umgebung: Linux-Server (Ubuntu), Docker Compose 2.30.3, Image `postgis/postgis` ohne Versionsangabe wie im
MOSIDI-Compose (PostgreSQL 18.6), Tabellendefinitionen aus MOSIDI-ORG/MOSIDI (Commit 26cadbb,
`tests/mosidi_tabellen.sql`). Quellen: WFS der Geobasis Brandenburg (`au:AdministrativeUnit`) und INKAR 2025.

| Schritt | Ergebnis |
|---|---|
| `docker compose run --rm test` | 9 Tests grün in 9 s |
| Lauf 1 (`lauf-1.json`, `.txt`) | 222 s; WFS: 432 gemeldet, 19 ausgefiltert (Land, Kreise), 413 Gemeinden neu; INKAR: 63.426.864 Zeilen gelesen, 1.285.774 (Gemeinden Brandenburg) neu |
| Lauf 2 (`lauf-2.json`, `.txt`) | 11 s; alles unverändert, Zieltabellen nicht angefasst; INKAR an der SHA-256 erkannt |
| Datenbank nach Lauf 2 | 413 Zeilen in „Kommunale Gebiete Brandenburg (InNoWest)“, MULTIPOLYGON, EPSG:4326, 29.653 km²; 1.285.774 Zeilen in `dashboard_data_de` mit `source = INKAR` (264 Indikatoren, 413 Gemeinden, 1995–2024); 413 von 413 INKAR-Gemeinden mit Geometrie über den Gemeindeschlüssel; `table_metadata`-Eintrag mit Ausdehnung |
| Im MOSIDI-Compose | frischer Klon von MOSIDI-ORG/MOSIDI, Ordner als `services/geoimport`, gestartet mit `docker-compose.mosidi.yml` und der MOSIDI-`.env`: Lauf 1 413 Gemeinden neu (5 s), Lauf 2 unverändert (5 s) |
