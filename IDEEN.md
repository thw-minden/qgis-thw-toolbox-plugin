# Roadmap & Ideen

Feature-Ideen in der geplanten Reihenfolge. Erledigte Punkte abhaken und in den `CHANGELOG.md` übernehmen; neue Ideen unten in den **Ideenspeicher** schreiben und später einem Meilenstein zuordnen.

Legende: `[x]` umgesetzt · `[~]` teilweise umgesetzt · `[ ]` offen

## Umgesetzt in 2.3.0

- [x] **Rückgängig machen (Strg+Z)**: Undo/Redo (Strg+Z, Strg+Y) für Objektplanung und taktische Zeichen – Setzen, Verschieben, Drehen, Größe, Beschriftung, Löschen, Duplizieren; bis zu 100 Schritte je Projekt (eigener Stapel: `planning/history.py`, `layer/marker_history.py`)
- [x] **Taktische Linien erweitern**: Linien mit Pfeilspitze am Anfang und/oder Ende und eigener Farbe, Deckkraft und Linienbreite (2.3.0)
- [x] **Radius / Gefahrenkreis** *(wichtig)*: Kreis mit fester Entfernung um einen Punkt ziehen (z. B. Absperrradius 500 m)

## Umgesetzt in 2.2.1

- [x] **Taktische Zeichen entfernen/löschen**: Zeichen auf der Karte auswählen (Klick, Shift+Klick, Auswahlrahmen) und mit Entf, Hotbar oder Rechtsklick-Menü löschen
- [x] **Zeichen kopieren/duplizieren** *(wichtig)*: D und Alt+Ziehen kopieren Zeichen inkl. Größe, Label, Drehung und Hintergrund – einzeln und in gemischter Auswahl
- [x] **Gebiete gut einzeichnen**: Einsatzabschnitt, Gefahrenbereich, Bereitstellungsraum, Absperrbereich mit eigenen Stilen; beschriften, verschieben, drehen, Punkte bearbeiten (Typen/Farben in `data/objektplanung.json`)
- [x] **Bewegungspfeile & Anmarschwege**: Pfeile mit Pfeilspitze in rot, blau, schwarz, grün frei zeichnen und beschriften
- [x] **CSV-/Excel-Import** für die Marker-Tabelle (`.csv`, `.tsv`, `.xlsx`, `.ods`) mit Spalten-Zuordnung, Vorschau und Vorlage
- [x] **Hotbar mit Favoriten & zuletzt benutzten Zeichen**: einheitlicher Zeichen-Picker mit Favoriten, Verlauf und Suche (Symbolpalette, Dialog, Rechtsklick auf die Karte); die Favoriten liegen in der Hotbar auf den Tasten 1–9, ohne Favoriten die zuletzt verwendeten Zeichen
- [x] **Hintergrundoptionen besser darstellen**: statt der Checkbox „Weißer Hintergrund“ ein Popup mit Vorschau für Form, Farbe, Deckkraft, Rand und Größe (`ui/background_popup.py`)

## Meilenstein 1 – Sicher bedienen & schnell zeichnen

Kleine bis mittlere Punkte mit dem größten Nutzen im Einsatz; bauen auf dem auf, was schon da ist.

- [~] **Gebiete per Koordinaten einzeichnen**: Eckpunkte als Koordinaten eingeben (UTMREF/MGRS oder Lat/Lon) statt nur mit der Maus
  - umgesetzt (2.3.0): Koordinaten-Tabelle in den Details (`ui/coordinate_table.py`) – Punkte eines vorhandenen Objekts als UTMREF bzw. Breite/Länge eintippen, einfügen und löschen
  - offen: ein Gebiet (oder eine Linie) ganz ohne Maus aus einer Koordinatenliste anlegen, z. B. „Gebiet aus Koordinaten …“ mit eingefügter Liste aus der Zwischenablage

## Meilenstein 2 – Einheiten & Darstellung

- [ ] **Stärke zu Einheiten hinzufügen**: Personalstärke (z. B. `1/2/6/9`) an Einheiten-Markern erfassen und anzeigen (neues Feld im Zeichen-Layer, Anzeige am Zeichen und in der Marker-Tabelle)
- [ ] **Gruppieren**: mehrere Marker/Einheiten zu einer Gruppe zusammenfassen (gemeinsam verschieben, ein-/ausblenden, Stärke aufsummieren) – setzt die Stärke voraus

## Meilenstein 3 – Objektplanung ausbauen

- [ ] **Pumpen mit Pumpleistung**: Pumpen maßstabsgetreu platzieren, Förderleistung (l/min) je Typ im Katalog hinterlegen und in der Bilanz aufsummieren (analog zu Stromerzeugern im Katalog `data/objektplanung.json`)
- [ ] **Flächen-Kapazität wieder zugänglich machen**: „Wie viele Zelte/Fahrzeuge passen in eine Fläche“ ist im Code vorhanden (`planning/capacity_dialog.py`, `TOOL_AREA` und `evaluate_selected_area()` in `planning/controller.py`), aber weiterhin weder über Hotbar, Dock noch Menü erreichbar
- [ ] **Notizen auf der Karte**: kleine Dokumente an einem Ort anheften, die Informationen zu dieser Stelle sammeln (z. B. Zufahrt, Ansprechpartner, Lagebeobachtungen)
  - Inhalt als Markdown mit optionalen Bildern (z. B. Fotos der Einsatzstelle), auf der Karte als Notiz-Symbol, Klick öffnet die Notiz zum Lesen und Bearbeiten
  - Text und Bilder im GeoPackage des Projekts speichern, damit Projekte ohne Plugin-Ordner vollständig bleiben
  - Basis vorhanden: Texte und Punkte der Objektplanung mit Beschreibung und `$POS`

## Meilenstein 4 – Karten & Vorlagen

- [ ] **Karten-Download**: Kartenausschnitt der Hintergrundkarte für die Offline-Nutzung herunterladen (MBTiles/GeoPackage)
  - Basis vorhanden: MBTiles-Export für Ebenenlayer (`export/dji_mbtiles_export.py`) und seit 2.3.0 für die ganze Objektplanung – beides exportiert die eigenen Inhalte, nicht die Hintergrundkarte; Nutzungsbedingungen der Kartendienste beachten
- [ ] **Vorlagen für Standardlagen**: fertige Zeichen-Sets für typische Lagen (z. B. Bereitstellungsraum) mit einem Klick einfügen – profitiert von Gruppieren und Duplizieren

## Ideenspeicher

Neue Ideen hier sammeln, bis sie einem Meilenstein zugeordnet sind.

- [ ] **Kopieren/Einfügen über die Zwischenablage** (Strg+C / Strg+V), z. B. an die Mausposition oder in ein anderes Projekt – Ergänzung zum Duplizieren; das Rückgängig machen (2.3.0) deckt das Einfügen dann gleich mit ab
