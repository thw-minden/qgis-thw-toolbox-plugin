# Changelog

## [Unreleased]

### Hinzugefügt
- **Annotationen zeichnen** über die neue Werkzeugleiste „THW Toolbox Annotationen“: Punkt (optional mit Beschreibung), Linie, Polygon und gefülltes Polygon (`tools/annotation_tool.py`, `layer/annotations.py`)
- Annotationen landen in einem QGIS-Annotations-Layer „THW Toolbox Annotationen“ und werden direkt in der Projektdatei gespeichert
- Standard-Linien- und Füllfarbe (inkl. Transparenz) sowie Standard-Linienbreite für Annotationen im Einstellungs-Dialog; die Linienbreite lässt sich je Objekt im Bearbeiten-Dialog ändern
- Neuer Dock-Tab **„Annotationen“** listet alle gezeichneten Objekte; ein Klick hebt das Objekt auf der Karte hervor und öffnet einen Dialog zum Ändern von Text, Linien- und Füllfarbe oder zum Löschen (`ui/annotation_dialog.py`)
- Werkzeug **„Annotation verschieben“**: Punkte sowie einzelne Stützpunkte von Linien und Polygonen per Ziehen verschieben; die Beschreibung eines Punktes wandert mit (`tools/annotation_move_tool.py`)
  - Linksklick auf eine Linie/einen Polygon-Umriss fügt einen Stützpunkt ein, Rechtsklick auf einen Stützpunkt entfernt ihn (Linien behalten mindestens 2, Polygone mindestens 3 Stützpunkte)
- Beschreibung auch für Linien und Polygone (im Bearbeiten-Dialog); sie erscheint mittig auf der Linie bzw. in der Fläche und lässt sich mit dem Verschieben-Werkzeug unabhängig von den Stützpunkten an eine andere Stelle ziehen
- Textgröße von Beschreibungen (Punkt, Linie, Polygon) mit dem Verschieben-Werkzeug über vier Eck-Anfasser am Beschreibungsrahmen skalieren (1–50 mm, Textrand wird mitskaliert; Punktbeschreibungen skalieren um ihre Mitte, Linien-/Polygonbeschreibungen um ihre Unterkante)
- Auch Beschreibungen von Punkten lassen sich frei verschieben (beim Verschieben des Punktes bleibt der Abstand erhalten); beim Überfahren, Verschieben oder Skalieren einer Beschreibung wird das zugehörige Objekt hervorgehoben
- Doppelklick auf eine Beschreibung (Verschieben-Werkzeug) öffnet den Bearbeiten-Dialog des zugehörigen Punktes, der Linie oder des Polygons
- Längenanzeige beim Zeichnen von Linien und Polygonen: neben dem Mauszeiger erscheinen die Länge des aktuellen Segments sowie die Gesamtlänge bzw. Umfang und Fläche (m², ab 1 ha zusätzlich in ha; ellipsoidisch gemessen)
- Bearbeiten-Dialog zeigt unter den Optionen einen Kasten „Messwerte“: Länge (Linien) bzw. Umfang und Fläche (Polygone); gemeinsame Zahlenformatierung in `util/units.py`
- Punkte können im Bearbeiten-Dialog einen Radius in Metern erhalten: maßstabsgetreuer, halbtransparenter Kreis um den Punkt (z. B. Gefahren- oder Sperrbereich); Umfang und Fläche des Kreises erscheinen unter „Messwerte“; der Radius kann bereits beim Setzen des Punktes angegeben werden (Standard: kein Radius)
- Mehrzeilige Beschreibungen für Punkte, Linien und Polygone (Enter = neue Zeile, Strg+Enter bestätigt den Dialog); in der Dock-Liste werden die Zeilen mit „/“ verbunden angezeigt
- Punktbeschreibungen werden zentriert gesetzt und neu angelegte stehen mittig über dem Punkt; bestehende linksbündige Punktbeschreibungen werden automatisch umgestellt, ohne ihre Position auf der Karte zu verändern
- Platzhalter `$POS` in Punktbeschreibungen wird auf der Karte durch die MGRS-/UTMRef-Koordinate des Punktes ersetzt (z. B. `32U MB 12345 98765`) und beim Verschieben des Punktes aktualisiert; Auflösung (0,1 / 1 / 10 / 100 m) projektweit im Einstellungs-Dialog; neue Funktion `point_to_mgrs` in `layout/mgrs_grid.py` (inkl. UTM-Sonderzonen Norwegen/Spitzbergen)
- Bearbeiten-Dialog: einklappbarer Kasten „Koordinaten (MGRS)“ mit allen Punkten/Stützpunkten; im Bearbeiten-Modus lassen sich Koordinaten eintippen sowie Stützpunkte mittig in eine Kante einfügen oder löschen (Mindestanzahl: Linie 2, Polygon 3); ungültige Eingaben werden markiert und verhindern das Übernehmen; neue Funktion `mgrs_to_point` (`layout/mgrs_grid.py`), Tabelle in `ui/coordinate_box.py`
- Beim Ziehen eines Punktes/Stützpunktes mit dem Verschieben-Werkzeug zeigt ein Hinweis am Mauszeiger dessen aktuelle MGRS-Koordinate (Projekt-Auflösung); gemeinsamer Mauszeiger-Hinweis in `tools/cursor_label.py`
- Verschieben-Werkzeug zeigt für jede Linie und jedes Polygon den Schwerpunkt (⊕); Ziehen am Schwerpunkt verschiebt das ganze Objekt samt Beschreibung (Vorschau und MGRS-Koordinate des neuen Schwerpunkts am Mauszeiger), Doppelklick öffnet den Bearbeiten-Dialog
- Doppelklick auf einen Punkt (Verschieben-Werkzeug) öffnet dessen Bearbeiten-Dialog
- Koordinaten-Tabelle im Bearbeiten-Dialog: Doppelklick auf eine Zeile kopiert die MGRS-Koordinate in die Zwischenablage (außerhalb des Bearbeiten-Modus)
- Adress-Suche (Alt+S) springt bei Eingabe einer MGRS-/UTMRef-Koordinate direkt dorthin (Zoom auf mind. 1:10.000) und hebt die Position kurz blinkend hervor

### Behoben
- Marker-Details zeigen statt „UTM 32N: …E …N“ (fest Zone 32, ohne 100-km-Quadrat) jetzt die vollständige MGRS-/UTMRef-Koordinate, z. B. `32U MB 12345 98765`; Zone und Band ergeben sich aus der Position, die Auflösung folgt der projektweiten Einstellung „MGRS-Auflösung“; „Kopieren“ kopiert nur die Koordinate
- Bei gesetztem Radius ist die Füllfarbe des Kreises im Bearbeiten-Dialog wählbar (Standard: Punktfarbe mit 25 % Deckkraft, folgt der Punktfarbe bis zur eigenen Wahl)
- Option im Bearbeiten-Dialog, Maße auf der Karte anzuzeigen: Radius eines Punktes (über dem Kreis), Segmentlängen einer Linie bzw. Kantenlängen eines Polygons (mittig an jeder Kante, entlang der Kante ausgerichtet); die Beschriftungen werden bei jeder Geometrieänderung automatisch aktualisiert

## [2.1.1]

### Verbessert

- Stille `except Exception: pass`-Blöcke durch `logger.debug(...)` ersetzt (Bandit B110): Cleanup-Fehler hinterlassen jetzt nachvollziehbare Spuren im QGIS-Log, ohne den Hauptablauf zu unterbrechen
- Bandit-Annotationen (`# nosec B404/B603/B607`) für die „Reveal in File Manager"-Aufrufe in `export/portable_export.py` (OS-Standard-Befehle `explorer`/`open`/`xdg-open`, kein User-Input)

## [2.1.0]

### Hinzugefügt
- **Drohnen-Export - Ebenenlayer (MBTiles)** über `DjiMbtilesExporter` für Vektor-Layer mit konfigurierbarem Zoom-Bereich
- **MGRS-Gitter** als temporärer Memory-Linelayer in der UTM-Zone der Karte (`layout/mgrs_grid.py`)
- **UTM-Gitter** für Drucklayouts mit einstellbaren Intervallen (`layout/utm_grid.py`)
- **Symbolbibliothek-Import** (`tools/style_library.py`): mitgelieferte THW-SVGs werden in die QGIS-Standard-Stilbibliothek importiert (filter- und entfernbar)
- Neues SVG-Asset für Unbemannte Luftfahrtsysteme (UAS)
- Neues `layout`-Paket als Container für Gitter- und Stilfunktionen

## [2.0.4]

### Hinzugefügt
- **UTM-Gitter** zur Druckkarte hinzufügen (Menüpunkt; automatische Erkennung des passenden UTM-Streifens und Intervalls)
- **MGRS-Gitter** als temporären Layer (Toolbar-Icon + Menüpunkt; eigenes `mgrs.svg`-Icon)
- **Drohnen-Export - Flugrouten (KMZ/KML)** über `DjiKmlExporter` mit Layer-Auswahldialog
- **Drohnen-Export - Ebenenlayer (MBTiles)** über `DjiMbtilesExporter` mit konfigurierbarem Zoom-Bereich
- **Mehrfach-Layer-Export** mit Fortschrittsanzeige, Abbrechen-Funktion und Sammel-Fehlerbericht
- **Symbolbibliothek-Verwaltung** im Setup-Dialog: THW-Taktische Zeichen als QGIS-Stile importieren/entfernen (`tools/style_library.py`)
- Neues Symbol-Asset für Unbemannte Luftfahrtsysteme (UAS)

### Verbessert
- **Portable Export**: überarbeitete Installationsanleitung und robusteres Kopieren der Ressourcen
- Logging-Level beim Portable Export auf `ERROR` reduziert (weniger Rauschen)
- **Qt6 / QGIS-4-Kompatibilität** für Feldtypen: bevorzugt `QMetaType.Type`, fällt sauber auf `QVariant.Type` zurück (verhindert stilles Verschwinden des `Bool`-Feldes beim Schema-Aufbau)
- Migration auf `QgsVectorFileWriter.writeAsVectorFormatV2` → `V3`
- Klarere, sichtbare Fehlermeldungen bei Layer-Schema-Erweiterung sowie bei Memory- und GeoPackage-Layer-Erstellung (statt stillem Drop einzelner Felder)
- Menüeinträge gekürzt: „THW Toolbox Setup/Einstellungen“ → „Toolbox Setup/Einstellungen“

### Behoben
- Stille `addAttribute`-Fehler beim Schema-Update werden jetzt erkannt und gemeldet
- Layer-Erstellung schlägt mit klarer Meldung fehl, wenn erwartete Felder im neuen Memory- oder GeoPackage-Layer fehlen
- Korrigierte Bandit-Annotation (`# nosec B310`) für den Nominatim-Request

## [2.0.1]

### Hinzugefügt
- Nominatim-Suchdialog für Adress- und Ortssuche inklusive passender Icons
- `SetupDialog` für Projekt-Status-Prüfung und Installation von Basemaps
- `TemplateDialog` zur Auswahl und Öffnung mitgelieferter Drucklayout-Vorlagen
- Neues Drucklayout `templates/Einsatz.qpt` inkl. Logo-Asset
- `OriginPointWidget` zur Auswahl von Transformations-Ankerpunkten (in `FeatureDock` integriert)
- Neue Navigationsleiste und Marker-Liste im SVG-Dock für bessere Übersicht

### Verbessert
- Projekt-Struktur grundlegend reorganisiert (`__init__.py`-Dateien, Entfernung unbenutzter Module wie Dock, DragMapTool, LayerManager, DropEventFilter, SelectionTool)
- Maximale Icon-Größe in `ConfigDialog` und `FeatureDock` erhöht
- Rotations-Slider im `FeatureDock` für feinere Kontrolle überarbeitet (kleinere Tick-Intervalle)
- Qt6-Kompatibilität und Mindest-QGIS-Version auf 3.44 angehoben
- SVG-Dateien der Schadenskonten (gelb/rot/weiß) bereinigt - redundante Pfade entfernt
- Code-Struktur und Lesbarkeit durch mehrere Refactorings verbessert (inkl. Ruff-Lint/Format)

### Behoben
- `IdentifyTool` und `MoveTool` prüfen nun die Layer-Gültigkeit, um Laufzeitfehler bei ungültigem Layer zu vermeiden
- Label-Offset-Berechnung für präzisere Positionierung korrigiert

## [2.0]

### Hinzugefügt
- Einstellungs-Dialog für Plugin-Konfiguration
- Konfigurierbare Standardwerte für Icon-Erstellung (Größe, Kartenskalierung)
- Label-Einstellungen (Schriftgröße, Buffer-Größe, Labels ein-/ausschalten)

### Verbessert
- Refactoring der Settings-Verwaltung mit zentraler `PluginSettings`-Klasse
- Einheitenumrechnung für Label-Darstellung (Millimeter statt Punkte)

### Behoben
- Korrektur der Einheitenumrechnung (UM zu MM)
- Fix für Dialog-Ergebnisprüfung (`== Accepted` statt `is not Rejected`)
- Numerische Werte werden konsistent als Integer gespeichert

## [1.3]

### Verbessert
- Stabilität der Drag & Drop-Funktionalität
- Korrekturen bei der Größenanpassung von Symbolen
- Korrekturen bei der Symbol-Bearbeitung

## [1.2]

### Hinzugefügt
- Erweiterte Symbol-Bibliothek mit über 1000 taktischen Zeichen
- Unterstützung für weitere Organisationen (Zoll, Wasserrettung)
- Verbesserte Dokumentation mit Preview-GIF
- Detaillierte README mit Schnellstart-Anleitung

### Verbessert
- Performance-Optimierungen für große Symbol-Sammlungen
- Benutzerfreundlichkeit der Symbol-Suche
- Stabilität der Drag & Drop-Funktionalität
- Feature-Identifizierung und -Bearbeitung

### Behoben
- Fehlerbehebungen bei der Symbol-Platzierung
- Verbesserte Kompatibilität mit verschiedenen QGIS-Versionen
- Korrekturen bei der Größenanpassung von Symbolen

## [1.1]

### Hinzugefügt
- Export-Funktion für portable Pakete
- Erweiterte Labeling-Funktionalität
- Mehrsprachige Symbol-Suche (Deutsch/Englisch)
- Feature-Dock für detaillierte Symbol-Bearbeitung

### Verbessert
- Intelligente Größenanpassung basierend auf Zoom-Faktor
- Optimierte SVG-Caching-Mechanismen
- Verbesserte Projekt-Integration

## [1.0]

### Hinzugefügt
- Grundlegende Drag & Drop-Funktionalität für Symbole
- Symbol-Dock mit Kategorien-Navigation
- Automatische Layer-Erstellung (GeoPackage)
- Feature-Identifizierung durch Klick
- Symbol-Verschiebung per Drag & Drop
- Dynamische Größenanpassung mit Schieberegler
- Labeling-System für Symbol-Beschriftungen
- Umfangreiche Symbol-Bibliothek für:
  - THW (Technisches Hilfswerk)
  - Bundeswehr
  - Feuerwehr
  - Polizei
  - Rettungswesen
  - Katastrophenschutz
- Automatisches Speichern von Änderungen
- Projekt-Integration mit automatischer Dateiverwaltung
- SVG-Embedding für maximale Portabilität

### Technische Details
- GeoPackage-basierte Datenspeicherung
- Punkt-Feature-Geometrie
- Automatische CRS-Anpassung
- Performance-Optimierungen (Throttling, Caching, Lazy Loading)

## [0.1] - 2024-01-XX

### Hinzugefügt
- Erste Veröffentlichung des Plugins
- Grundlegende Drag & Drop-Funktionalität
- Feature-Identifizierung und -Bearbeitung
- Umfangreiche Symbol-Bibliothek
- Export-Funktionen für portable Pakete

