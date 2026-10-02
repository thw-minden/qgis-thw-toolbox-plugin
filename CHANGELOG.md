# Changelog

## [Unreleased]

### Hinzugefügt
- **THW-Drucklayouts A0–A4 quer** (Standard der THW-Leitung) in `templates/` inkl. Logo und Nordpfeil
- Druckvorlagen-Dialog fragt Ortsverband, Einheit, Bearbeiter, Einsatz und Einstufung (VS-NfD / nicht klassifiziert) ab; OV/Einheit/Bearbeiter werden in den Benutzereinstellungen, der Einsatz im Projekt gemerkt
- Autovervollständigung für den Ortsverband aus `data/ovs.json` (670 OVs, Teilwortsuche)
- Platzhalter der Vorlagen werden zu Layout-Variablen (`@thw_ov`, `@thw_einheit`, `@thw_einheit_kurz`, `@thw_bearbeiter`, `@thw_einsatz`), nachträglich in den Layout-Eigenschaften änderbar
- **Objektplanung** (Dock + schwebende Hotbar unten auf der Karte, Paket `planning/`; Standardwerkzeug ist die Auswahl, Esc führt nach dem Platzieren dorthin zurück):
  - Zelte maßstabsgetreu platzieren (SG 300, SG 500, SG 20/30/50, Faltpavillons, eigenes Maß) inkl. gestrichelter Abspannung, Drehen per R / Strg+Mausrad / Rechtsklick; Mindestabstand wird geprüft und nur als Warnung gemeldet
  - **Fahrzeuge** maßstabsgetreu platzieren (GKW, MTW Sprinter, MTW T5/T6, MzKW, MzGW, FüKW, MLW, LKW-K, LKW Ladekran, WLF, Radlader, PKW, Anhänger u.a.) in Draufsicht mit Fahrerhaus bzw. Deichsel, dem passenden **THW-Fahrzeugzeichen** in der Mitte und eigenem Mindestabstand
  - **Raster**: Zelte/Fahrzeuge als Reihen × Spalten im Mindestabstand auf einen Klick setzen
  - **Hilfslinien**: beim Platzieren und Verschieben an Kanten und Mitten benachbarter Zelte/Fahrzeuge (auch im Mindestabstand) einrasten; Strg hält das Einrasten an
  - **Bearbeiten im Figma-Stil** wie bei den taktischen Zeichen: blauer Rahmen mit Eckpunkten und Maß-Badge, Hover-Umriss, Klick / Shift+Klick / Auswahlrahmen, Ziehen verschiebt (Badge zeigt den Abstand zum Nachbarn), an den Ecken ziehen dreht (Shift: 15°), Alt+Ziehen kopiert, Pfeiltasten schieben 0,5 m (Shift: 5 m), Entf löscht, R dreht, D dupliziert, Doppelklick / Enter bearbeitet die Bezeichnung, Rechtsklick-Menü
  - **Flächen-Kapazität**: Fläche zeichnen oder auswählen → Tabelle, wie viele Zelte bzw. Fahrzeuge je Typ hineinpassen, Vorschau auf der Karte und Übernahme per Klick
  - **Stromversorgung**: Stromerzeuger (SEA 5/8/13 kVA, NEA 50/200 kVA) mit taktischem Zeichen, Leitungsroller 25 m / 50 m (fängt Stromerzeuger, Verteiler, Leuchten und Leitungsenden), Verteiler 16 A; lange Strecken werden automatisch in mehrere Trommeln aufgeteilt, Kupplungspunkte und Rest der letzten Trommel werden angezeigt
  - **Beleuchtung**: Flutlicht, LED-Strahler, Leuchtballone, Lichtmast mit taktischem Zeichen und ausgeleuchtetem Radius auf der Karte
  - Bilanz (Zelte/Zeltfläche, Fahrzeuge, Leitungslänge, Verteiler, Stromerzeuger, Beleuchtung) inkl. **Last je Stromerzeuger** über verbundene Leitungen und Hinweis auf nicht angeschlossene Leuchten
  - Speicherung in `<projekt>_objektplanung.gpkg` neben der Projektdatei; Katalog mit Maßen und Leistungen in `data/objektplanung.json`

### Verbessert
- **Einheitliche Legende**: jedes taktische Zeichen erscheint einmal, ungedreht und in fester Größe (max. Symbolgröße der Vorlage), unabhängig von Größe/Drehung auf der Karte (`layout/print_template.py`)

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

