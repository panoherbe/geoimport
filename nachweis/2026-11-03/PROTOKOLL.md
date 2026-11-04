> Text am 04.11.2026 nachgebessert: Name der Zieltabelle in den Berichten dieses Ordners gekürzt; Zahlen unverändert.

# Nachlauf geoimport 0.2, 03.11.2026 (Tests auch am 04.11.2026)

Zweck: prüfen, ob ein frischer Lauf am Folgetag dieselben Zahlen liefert wie die Messung vom 02.11.2026
(`nachweis/2026-11-02/`). Umgebung wie dort: Linux-Server (Ubuntu), Docker Compose, `postgis/postgis` ohne
Versionsangabe; Code Stand `de14357` (0.2.0); Quellen WFS der Geobasis Brandenburg und INKAR 2025.

| Schritt | Ergebnis |
|---|---|
| Lauf 1 ([`lauf-1.txt`](lauf-1.txt)) | 413 Gemeinden neu, 1.285.774 INKAR-Zeilen neu, beide Quellen ersetzt; Laufzeit 668,8 s (der Server rechnete zur selben Zeit an einem anderen Auftrag; ohne diese Last am 02.11.2026: 221,5 s) |
| Lauf 2 ([`lauf-2.txt`](lauf-2.txt)) | alles unverändert, Zieltabellen nicht angefasst; INKAR an der SHA-256 erkannt |
| Datenbank nach Lauf 2 | 413 Zeilen mit 413 verschiedenen Gemeindeschlüsseln, zusammen 29.653 km²; 1.285.774 Zeilen mit `source = INKAR`, 264 Indikatoren, 413 Gemeinden, Jahre 1995–2024; 413 von 413 INKAR-Gemeinden finden ihre Geometrie über den Gemeindeschlüssel |
| Tests am 03.11.2026 ([`tests-2026-11-03.txt`](tests-2026-11-03.txt)) | 9 grün |
| Tests am 04.11.2026 ([`tests-2026-11-04.txt`](tests-2026-11-04.txt)) | 9 grün, mit der eigenen, minimalen Tabellendefinition `tests/mosidi_tabellen.sql`, die seit 04.11.2026 die aus MOSIDI übernommene Datei ersetzt |

INKAR-Datei, am 04.11.2026 um 02:56 UTC per HTTP-HEAD auf
`https://www.bbr-server.de/imagemap/inkar/download/inkar_2025.zip` gelesen: 433.790.695 Byte (434 MB), zuletzt
geändert am 13.08.2025.

Die Kontrollkarte im Rundgang stammt aus der Datenbank dieses Nachlaufs (Durchschnittsalter der Bevölkerung 2023,
INKAR-Kürzel `m_bev_alter`).
