# Changelog

## [Unreleased]

### Hinzugefügt
- **Druckvorlagen der Toolbox A4/A3 quer** (`Toolbox_*.qpt`, im Dialog unter „THW Toolbox“; Schrift BundesSans), reduziert auf das Nötige und schwarz auf weiß ohne Flächenfüllungen: Titelleiste mit Bundeslogo, Kartentitel samt Einsatz/Ort und rechts dem taktischen Zeichen des Trupps UL (im Dialog abschaltbar); in der Karte Nordpfeil oben rechts und Maßstab unten links wie in den Vorlagen der THW-Leitung, beide auf durchscheinendem Grund; alle übrigen Angaben in der Seitenleiste (Zeichenerklärung, eine Übersicht, Stand, Gitter, Herausgeber, Quellen)
  - **Gitter wählbar**: UTMREF (Maschenweite 10 m / 100 m / 1 km / 10 km je nach Maßstab) oder Lon/Lat in Dezimalgrad, jeweils mit Randbeschriftung; die Seitenleiste nennt das Gitter, den Linienabstand und die Koordinate der Kartenmitte als Lesebeispiel (`@thw_gitter`)
  - Kartenausschnitt und UTM-Zone werden beim Öffnen aus dem Kartenfenster übernommen, der Maßstab auf einen gängigen Wert aufgerundet
  - Neue Felder „Kartentitel“ (mit Vorschlägen) und „Einsatzort“ im Druckvorlagen-Dialog (`@thw_kartentitel`, `@thw_einsatzort`), im Projekt gespeichert; „Blatt“ (`@thw_blatt`) erscheint nur, wenn angegeben
  - Abfrage der Berechtigung für das Bundeslogo beim Öffnen (merkbar; alternativ „Ohne Logo öffnen“)
  - Erzeugt mit `scripts/build_print_templates.py`
- **Eigene Druckvorlagen**: „Eigene hinzufügen …“ im Druckvorlagen-Dialog übernimmt beliebige `.qpt`-Dateien in das QGIS-Profil (`thw_toolbox/druckvorlagen`, übersteht Plugin-Updates), „Entfernen“ löscht sie wieder; die Angaben aus dem Dialog stehen darin als Layout-Variablen bereit. Die Vorlagen sind nach „THW Toolbox“, „THW-Leitung“ und „Eigene Vorlagen“ gruppiert
- **Zeichenerklärung nach Wahl**: Die Legende zeigt nur, was auf die Grundkarte gezeichnet wurde (taktische Zeichen, Objektplanung, eigene Vektorlayer im Kartenausschnitt) – ohne Hintergrundkarten, Gitter-Layer, leere Gruppen und Gruppenüberschriften; Zeichennamen ohne Unterstriche. Einzelne Layer lassen sich im Dialog an- und abhaken (am Layer im Projekt gespeichert); „Layout → Legende aktualisieren (THW Toolbox)“ im Designer baut sie nach Änderungen neu auf
- Übersichtskarten der Druckvorlagen zeigen nur noch die Hintergrundkarte, nicht mehr Zeichen und deren Beschriftungen
- **A3 aus zwei A4-Blättern**: Im Layout-Designer exportiert „Layout → Als A4-Blätter exportieren (PDF) …“ jedes Layout maßstabstreu als mehrseitiges A4-PDF zum Zusammenkleben (5 mm Druckrand, 10 mm Überlappung, Schnittlinie auf den Folgeblättern). Die Vorlage `Toolbox_A3_QUER_2xA4` (400 × 297 mm) passt genau auf zwei A4-Blätter hoch
- **THW-Drucklayouts A0–A4 quer** (Standard der THW-Leitung) in `templates/` inkl. Logo und Nordpfeil
- Druckvorlagen-Dialog fragt Ortsverband, Einheit, Bearbeiter und Einsatz ab; OV/Einheit/Bearbeiter werden in den Benutzereinstellungen, der Einsatz im Projekt gemerkt
- Keine Einstufung VS-NfD: eingestufte Inhalte dürfen mit QGIS nicht verarbeitet werden. Die Toolbox-Vorlagen tragen keine Kennzeichnung, die ausgeblendete VS-NfD-Kennzeichnung der Vorlagen der THW-Leitung wird beim Laden entfernt
- Autovervollständigung für den Ortsverband aus `data/ovs.json` (670 OVs, Teilwortsuche)
- Platzhalter der Vorlagen werden zu Layout-Variablen (`@thw_ov`, `@thw_einheit`, `@thw_einheit_kurz`, `@thw_bearbeiter`, `@thw_einsatz`), nachträglich in den Layout-Eigenschaften änderbar
- **Objektplanung** (Dock + schwebende Hotbar unten auf der Karte, Paket `planning/`; Standardwerkzeug ist die Auswahl, Esc führt nach dem Platzieren dorthin zurück):
  - Zelte maßstabsgetreu platzieren (SG 300, SG 500, SG 20/30/50, Faltpavillons, eigenes Maß) inkl. gestrichelter Abspannung, Drehen per R / Strg+Mausrad / Rechtsklick; Mindestabstand wird geprüft und nur als Warnung gemeldet
  - **Fahrzeuge** maßstabsgetreu platzieren (GKW, MTW Sprinter, MTW T5/T6, MzKW, MzGW, FüKW, MLW, LKW-K, LKW Ladekran, WLF, Radlader, PKW, Anhänger u.a.) in Draufsicht mit Fahrerhaus bzw. Deichsel, dem passenden **THW-Fahrzeugzeichen** in der Mitte und eigenem Mindestabstand
  - **Raster**: Zelte/Fahrzeuge als Reihen × Spalten im Mindestabstand auf einen Klick setzen; das Raster gilt für einen Klick, danach wird wieder einzeln gesetzt
  - **Hilfslinien**: beim Platzieren und Verschieben an Kanten und Mitten benachbarter Zelte/Fahrzeuge (auch im Mindestabstand) einrasten; Strg hält das Einrasten an
  - **Bearbeiten im Figma-Stil** wie bei den taktischen Zeichen: blauer Rahmen mit Eckpunkten und Maß-Badge, Hover-Umriss, Klick / Shift+Klick / Auswahlrahmen, Ziehen verschiebt (Badge zeigt den Abstand zum Nachbarn), an den Ecken ziehen dreht (Shift: 15°), Alt+Ziehen kopiert, Pfeiltasten schieben 0,5 m (Shift: 5 m), Entf löscht, R dreht, D dupliziert, Doppelklick / Enter bearbeitet die Bezeichnung, Rechtsklick-Menü
  - **Ein Cursor für alles**: Die Hotbar und das Auswahlwerkzeug gelten für taktische Zeichen und Objektplanung gemeinsam (sichtbar, sobald Toolbox oder Objektplanung offen ist). Ein Klick auf ein Zeichen wählt das Zeichen (Rahmen zum Skalieren/Drehen wie bisher), ein Klick auf ein Objekt das Objekt; rechts kommt automatisch der passende Tab nach vorn (Symbolpalette und Objektplanung liegen als Tabs übereinander). Auswahlrahmen und Shift+Klick erfassen Zeichen und Objekte gemischt – gemeinsam verschieben und löschen. Karte verschieben: mittlere Maustaste oder Leertaste
  - **Drag & Drop**: Zelte, Fahrzeuge, Verteiler, Stromerzeuger und Leuchten lassen sich aus dem Dock direkt auf die Karte ziehen
  - **Gebiete** (Einsatzabschnitt, Gefahrenbereich, Bereitstellungsraum, Absperrbereich) und **Pfeile** (rot, blau, schwarz, grün) frei zeichnen, beschriften, verschieben, drehen und duplizieren; Typen/Farben in `data/objektplanung.json`. **Punkte bearbeiten** (Doppelklick, Hotbar oder Rechtsklick-Menü): Eckpunkte ziehen, auf den Kantenmitten neue Punkte herausziehen, Rechtsklick / Entf löscht einen Punkt
  - **Taktische Zeichen duplizieren**: Alt+Ziehen und D kopieren jetzt auch Zeichen (einzeln und in gemischter Auswahl); Zeichen lassen sich an den Ecken bis Größe 1 verkleinern (bisher 10)
  - **Hotbar abschaltbar**: Einstellungen → „Hotbar auf der Karte anzeigen“; die Werkzeuge bleiben im Dock erreichbar
  - Die Flächen-Kapazität (wie viele Zelte/Fahrzeuge passen in eine Fläche) ist vorerst nicht mehr über die Oberfläche erreichbar
  - **Stromversorgung**: Stromerzeuger (SEA 5/8/13 kVA, NEA 50/200 kVA) mit taktischem Zeichen, Leitungsroller 25 m / 50 m (fängt Stromerzeuger, Verteiler, Leuchten und Leitungsenden), Verteiler 16 A; lange Strecken werden automatisch in mehrere Trommeln aufgeteilt, Kupplungspunkte und Rest der letzten Trommel werden angezeigt
  - **Beleuchtung**: Flutlicht, LED-Strahler, Leuchtballone, Lichtmast mit taktischem Zeichen und ausgeleuchtetem Radius auf der Karte
  - Bilanz (Zelte/Zeltfläche, Fahrzeuge, Leitungslänge, Verteiler, Stromerzeuger, Beleuchtung) inkl. **Last je Stromerzeuger** über verbundene Leitungen und Hinweis auf nicht angeschlossene Leuchten
  - Speicherung in `<projekt>_objektplanung.gpkg` neben der Projektdatei; Katalog mit Maßen und Leistungen in `data/objektplanung.json`
- **Marker-Tabelle: Import aus CSV/Excel** (`.csv`, `.tsv`, `.xlsx`, `.ods`) mit Spalten-Zuordnung für Position (UTMREF, „Breite Länge“ oder getrennte Spalten), Beschriftung und Zeichen, Standard-Zeichen und Vorschau; unvollständige Zeilen bleiben als Entwurf in der Tabelle; „Vorlage speichern …“ legt eine Beispieltabelle zum Ausfüllen an

### Verbessert
- **Zeichen-Auswahl** (Icon-Picker): Namen werden vollständig und mehrzeilig angezeigt statt abgekürzt, das Raster füllt die ganze Breite, das gewählte Zeichen steht mit Kategorie unter dem Raster, Pfeiltasten im Suchfeld bewegen die Auswahl
- Marker-Tabelle hat ein eigenes Toolbar-Icon im Stil der übrigen
- **Einheitliche Legende**: jedes taktische Zeichen erscheint einmal, ungedreht und in fester Größe (max. Symbolgröße der Vorlage), unabhängig von Größe/Drehung auf der Karte (`layout/legend.py`)
- Der Layout-Designer öffnet sich aus dem Druckvorlagen-Dialog im Vordergrund statt hinter dem QGIS-Hauptfenster

### Entfernt
- Drucklayout `templates/Einsatz.qpt`

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

