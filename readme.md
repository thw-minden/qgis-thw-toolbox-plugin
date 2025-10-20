# THW Toolbox Plugin

Ein QGIS-Plugin für das einfache Hinzufügen und Verwalten von taktischen Zeichen auf der Karte.

## Beschreibung

Das THW Toolbox Plugin ermöglicht es, SVG-Symbole (taktische Zeichen) per Drag & Drop oder Klick auf der Karte zu platzieren. Es wurde speziell für den Einsatz im Technischen Hilfswerk (THW) entwickelt und enthält eine umfangreiche Sammlung von taktischen Zeichen für verschiedene Organisationen und Einsatzbereiche.

## Hauptfunktionen

### 🎯 Symbol-Platzierung
- **Drag & Drop**: Ziehen Sie Symbole aus dem Dock direkt auf die Karte
- **Intelligente Größenanpassung**: Symbole werden automatisch an den aktuellen Zoom-Faktor angepasst
- **Persistente Speicherung**: Alle Symbole werden in einer GeoPackage-Datei gespeichert

### 🔍 Feature-Management
- **Identifizierung**: Klicken Sie auf Symbole, um Details anzuzeigen
- **Verschieben**: Symbole können mit dem Mauszeiger verschoben werden
- **Größenanpassung**: Dynamische Größenänderung mit Schieberegler
- **Labeling**: Beschriftung der Symbole mit anpassbarem Text

### 📁 Symbol-Bibliothek
Das Plugin enthält Symbole für:
- **THW** (Technisches Hilfswerk)
- **Bundeswehr**
- **Feuerwehr**
- **Polizei**
- **Rettungswesen**
- **Katastrophenschutz**
- **Wasserrettung**
- **Zoll**
- Und viele weitere Kategorien

### 💾 Datenverwaltung
- **Automatisches Speichern**: Änderungen werden automatisch gespeichert
- **Projekt-Integration**: Layer-Dateien werden beim Speichern des Projekts verschoben
- **Portable Pakete**: Export-Funktion für vollständig portable Symbol-Sammlungen

## Installation

### Voraussetzungen
- QGIS 3.0 oder höher
- Python 3.x

### Installationsschritte
1. Laden Sie das Plugin herunter oder kopieren Sie es in Ihr QGIS Plugin-Verzeichnis:
   - **Windows**: `%APPDATA%/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

2. Aktivieren Sie das Plugin in QGIS:
   - Gehen Sie zu `Plugins` → `Verwalten und installieren`
   - Aktivieren Sie "THW Toolbox" in der Liste der verfügbaren Plugins

3. Das Plugin-Symbol erscheint in der QGIS-Toolbar

## Verwendung

### Plugin aktivieren
1. Klicken Sie auf das THW Toolbox-Symbol in der Toolbar
2. Das Symbol-Dock öffnet sich rechts in QGIS
3. Ein neuer Layer "THW Toolbox Marker" wird automatisch erstellt

### Symbole platzieren
1. **Drag & Drop**: Ziehen Sie ein Symbol aus dem Dock auf die gewünschte Position auf der Karte
2. **Klick-Modus**: Wählen Sie ein Symbol aus und klicken Sie auf die Karte

### Symbole bearbeiten
1. **Identifizieren**: Klicken Sie auf ein Symbol, um es auszuwählen
2. **Details anzeigen**: Das Feature-Dock zeigt alle Eigenschaften des Symbols
3. **Verschieben**: Halten Sie die linke Maustaste gedrückt und ziehen Sie das Symbol
4. **Größe ändern**: Verwenden Sie den Schieberegler im Feature-Dock
5. **Label hinzufügen**: Aktivieren Sie "Label anzeigen" und geben Sie Text ein

### Symbol-Suche
- Verwenden Sie die Suchleiste im Symbol-Dock, um schnell Symbole zu finden
- Die Suche funktioniert sowohl mit deutschen als auch englischen Begriffen

## Technische Details

### Datenformat
- **Layer-Typ**: GeoPackage (.gpkg)
- **Geometrie**: Punkt-Features
- **Symbole**: SVG-Dateien mit eingebettetem Inhalt
- **Koordinatensystem**: Automatische Anpassung an das Projekt-CRS

### Felder
Jedes Symbol-Feature enthält folgende Attribute:
- `name`: Name der SVG-Datei
- `svg_path`: Relativer Pfad zur SVG-Datei
- `svg_content`: Vollständiger SVG-Inhalt (für Portabilität)
- `size`: Symbolgröße in Map Units
- `scale_with_map`: Ob das Symbol mit der Karte skalieren soll
- `unique_id`: Eindeutige Identifikation
- `label`: Beschriftungstext
- `show_label`: Ob die Beschriftung angezeigt werden soll

### Performance-Optimierungen
- **Intelligente Toleranz**: Feature-Erkennung basiert auf Symbolgröße
- **Throttling**: Aktualisierungen werden gedrosselt für bessere Performance
- **Caching**: SVG-Icons werden gecacht für schnelle Anzeige
- **Lazy Loading**: Symbol-Ordner werden nur bei Bedarf geladen

## Export-Funktionen

### Portables Paket erstellen
1. Gehen Sie zu `Plugins` → `THW Toolbox` → `Portables Paket exportieren`
2. Wählen Sie einen Zielordner
3. Das Plugin erstellt ein ZIP-Archiv mit:
   - Allen SVG-Symbolen
   - Der GeoPackage mit allen platzierten Symbolen
   - Installationsanweisungen

## Lizenzinformationen

### Externe Ressourcen
- **Taktische Zeichen**: CC BY 4.0 (Creative Commons Attribution 4.0 International)
  - Quelle: https://github.com/jonas-koeritz/Taktische-Zeichen
- **Google Roboto Font**: Apache 2.0 (Apache License, Version 2.0)
  - Quelle: https://fonts.google.com/specimen/Roboto

### Plugin-Lizenz
Dieses Plugin steht unter der MIT-Lizenz zur Verfügung.

## Support und Entwicklung

### Autor
- **Entwickler**: Paul Horstmann
- **E-Mail**: paul.horstmann@thw-minden.de
- **Organisation**: THW Minden

### Fehlerbehebung
Bei Problemen überprüfen Sie:
1. QGIS-Version (mindestens 3.0 erforderlich)
2. Schreibrechte im Plugin-Verzeichnis
3. Verfügbarkeit der SVG-Dateien
4. Log-Datei: `svg_dock.log` im Plugin-Verzeichnis

### Bekannte Einschränkungen
- Symbole werden nur in Punkt-Layern unterstützt
- Sehr große SVG-Dateien können die Performance beeinträchtigen
- Bei sehr vielen Symbolen kann die Darstellung verlangsamt werden

## Changelog

### Version 0.1
- Erste Veröffentlichung
- Grundlegende Drag & Drop-Funktionalität
- Feature-Identifizierung und -Bearbeitung
- Umfangreiche Symbol-Bibliothek
- Export-Funktionen für portable Pakete

## Beitragen

Verbesserungsvorschläge und Bug-Reports sind willkommen! Kontaktieren Sie den Entwickler oder erstellen Sie ein Issue im Projekt-Repository.

---

**Hinweis**: Dieses Plugin wurde für den Einsatz im THW entwickelt, kann aber auch für andere Organisationen und Zwecke verwendet werden.
