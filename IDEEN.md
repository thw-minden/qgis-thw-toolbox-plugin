# Roadmap & Ideen

Feature-Ideen in der geplanten Reihenfolge. Erledigte Punkte abhaken und in den `CHANGELOG.md` übernehmen; neue Ideen unten in den **Ideenspeicher** schreiben und später einem Meilenstein zuordnen.

Legende: `[x]` umgesetzt · `[~]` teilweise umgesetzt · `[ ]` offen

## Umgesetzt (im nächsten Release, siehe `CHANGELOG.md` → Unreleased)

- [x] **Taktische Zeichen entfernen/löschen**: Zeichen auf der Karte auswählen (Klick, Shift+Klick, Auswahlrahmen) und mit Entf, Hotbar oder Rechtsklick-Menü löschen
- [x] **Zeichen kopieren/duplizieren** *(wichtig)*: D und Alt+Ziehen kopieren Zeichen inkl. Größe, Label, Drehung und Hintergrund – einzeln und in gemischter Auswahl
- [x] **Gebiete gut einzeichnen**: Einsatzabschnitt, Gefahrenbereich, Bereitstellungsraum, Absperrbereich mit eigenen Stilen; beschriften, verschieben, drehen, Punkte bearbeiten (Typen/Farben in `data/objektplanung.json`)
- [x] **Bewegungspfeile & Anmarschwege**: Pfeile mit Pfeilspitze in rot, blau, schwarz, grün frei zeichnen und beschriften
- [x] **CSV-/Excel-Import** für die Marker-Tabelle (`.csv`, `.tsv`, `.xlsx`, `.ods`) mit Spalten-Zuordnung, Vorschau und Vorlage

## Meilenstein 1 – Sicher bedienen & schnell zeichnen

Kleine bis mittlere Punkte mit dem größten Nutzen im Einsatz; bauen auf dem auf, was schon da ist.

- [ ] **Rückgängig machen (Strg+Z)**: Setzen, Verschieben, Drehen, Löschen und Duplizieren von Zeichen und Objekten per Undo/Redo korrigieren
  - Hinweis: Änderungen werden heute direkt gespeichert (Commit bzw. Datenprovider), der QGIS-Undo-Stack greift daher nicht → eigener Undo-Stapel über Zeichen- und Objektplanungs-Layer nötig
- [ ] **Radius / Gefahrenkreis** *(wichtig)*: Kreis mit fester Entfernung um einen Punkt ziehen (z. B. Absperrradius 500 m)
  - mehrere konzentrische Kreise auf einmal (z. B. 100 / 250 / 500 m), Abstände frei einstellbar
  - Anwendungsfall **Personensuche**: Suchringe um den letzten bekannten Aufenthaltsort, Ringe beschriftet und unterschiedlich eingefärbt
  - Basis vorhanden: Gebiete-Layer und der Radius-Kreis der Beleuchtung in der Objektplanung
- [~] **Hotbar mit Favoriten & zuletzt benutzten Zeichen**: Die Hotbar auf der Karte gibt es (Werkzeuge der Objektplanung, Auswahl-Aktionen). Offen: anpinnbare Favoriten + automatisch die zuletzt benutzten taktischen Zeichen, evtl. per Zifferntasten 1–9
- [ ] **Gebiete per Koordinaten einzeichnen**: Eckpunkte als Koordinaten eingeben (UTMREF/MGRS oder Lat/Lon) statt nur mit der Maus
  - Basis vorhanden: Koordinaten-Parser (`util/coordinates.py`) und Positionsdialog der Zeichen

## Meilenstein 2 – Einheiten & Darstellung

- [ ] **Stärke zu Einheiten hinzufügen**: Personalstärke (z. B. `1/2/6/9`) an Einheiten-Markern erfassen und anzeigen (neues Feld im Zeichen-Layer, Anzeige am Zeichen und in der Marker-Tabelle)
- [ ] **Gruppieren**: mehrere Marker/Einheiten zu einer Gruppe zusammenfassen (gemeinsam verschieben, ein-/ausblenden, Stärke aufsummieren) – setzt die Stärke voraus
- [ ] **Hintergrundoptionen besser darstellen**: bisher nur die Checkbox „Weißer Hintergrund“; Auswahl übersichtlicher gestalten (z. B. mit Vorschau, weitere Formen/Farben)
- [~] **Taktische Linien erweitern**: Pfeile gibt es bisher nur nach Farbe; offen sind benannte Typen (Vormarsch, Rückzug, Anmarsch-/Abfahrtsweg) mit eigenem Linienstil

## Meilenstein 3 – Objektplanung ausbauen

- [ ] **Pumpen mit Pumpleistung**: Pumpen maßstabsgetreu platzieren, Förderleistung (l/min) je Typ im Katalog hinterlegen und in der Bilanz aufsummieren (analog zu Stromerzeugern im Katalog `data/objektplanung.json`)
- [ ] **Flächen-Kapazität wieder zugänglich machen**: „Wie viele Zelte/Fahrzeuge passen in eine Fläche“ ist im Code vorhanden (`planning/capacity_dialog.py`), aber derzeit nicht über die Oberfläche erreichbar

## Meilenstein 4 – Karten & Vorlagen

- [ ] **Karten-Download**: Kartenausschnitt der Hintergrundkarte für die Offline-Nutzung herunterladen (MBTiles/GeoPackage)
  - Basis vorhanden: MBTiles-Export für Ebenenlayer (`export/dji_mbtiles_export.py`); Nutzungsbedingungen der Kartendienste beachten
- [ ] **Vorlagen für Standardlagen**: fertige Zeichen-Sets für typische Lagen (z. B. Bereitstellungsraum) mit einem Klick einfügen – profitiert von Gruppieren und Duplizieren

## Ideenspeicher

Neue Ideen hier sammeln, bis sie einem Meilenstein zugeordnet sind.

- [ ] **Kopieren/Einfügen über die Zwischenablage** (Strg+C / Strg+V), z. B. an die Mausposition oder in ein anderes Projekt – Ergänzung zum Duplizieren
