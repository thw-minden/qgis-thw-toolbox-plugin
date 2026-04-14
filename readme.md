# THW Toolbox Plugin

![Plugin-Vorschau](docs/preview.gif)

Ein QGIS-Plugin für das einfache Hinzufügen und Verwalten von taktischen Zeichen auf der Karte. Entwickelt speziell für den Einsatz im Technischen Hilfswerk (THW) und anderen Einsatzorganisationen.

---

## Schnellstart

1. **Plugin aktivieren** — Klicken Sie auf das THW Toolbox-Symbol in der QGIS-Toolbar
2. **Symbol auswählen** — Wählen Sie ein Symbol aus dem Dock aus
3. **Platzieren** — Ziehen Sie das Symbol auf die Karte oder klicken Sie auf die gewünschte Position
4. **Bearbeiten** — Klicken Sie auf ein Symbol, um es zu verschieben, zu skalieren oder zu beschriften

---

## Hauptfunktionen

### Symbol-Platzierung

- **Drag & Drop** — Symbole direkt aus dem Dock auf die Karte ziehen
- **Klick-Modus** — Symbol auswählen und auf die gewünschte Position klicken
- **Intelligente Größenanpassung** — Symbole werden automatisch an den Zoom-Faktor angepasst
- **Persistente Speicherung** — Alle Symbole werden automatisch in einer GeoPackage-Datei gespeichert

### Feature-Management

- **Identifizierung** — Klick auf Symbole zeigt Details an
- **Verschieben** — Symbole per Maus an neue Position ziehen
- **Größenanpassung** — Dynamische Größenänderung mit Schieberegler
- **Labeling** — Beschriftung mit anpassbarem Text und Positionierung
- **Echtzeit-Vorschau** — Sofortige visuelle Rückmeldung bei Änderungen

### Einstellungen (neu in 2.0)

- **Einstellungs-Dialog** — Zentrale Konfiguration über das Zahnrad-Icon im Dock
- **Icon-Standardwerte** — Standardgröße und Kartenskalierung konfigurierbar
- **Label-Konfiguration** — Schriftgröße, Buffer-Größe und Labels ein-/ausschalten

### Symbol-Bibliothek

Umfassende Sammlung von über 1000 taktischen Zeichen:

| Kategorie | Inhalte |
|-----------|---------|
| THW | Einheiten, Fahrzeuge, Personen, Gebäude |
| Bundeswehr | Einheiten, Fahrzeuge, Personen |
| Feuerwehr | Einheiten, Fahrzeuge, Personen, Gebäude |
| Polizei | Einheiten, Fahrzeuge |
| Rettungswesen | Einheiten, Fahrzeuge, Personen, Einrichtungen |
| Katastrophenschutz | Einheiten, Fahrzeuge |
| Wasserrettung | Einheiten, Fahrzeuge, Personen, Einrichtungen |
| Zoll | Einheiten, Fahrzeuge |
| Einrichtungen | Führungsstellen, Versorgungsstellen, Behandlungsplätze |
| Gefahren | Verschiedene Gefahrensymbole |
| Maßnahmen | Einsatzmaßnahmen und Aktionen |
| Schäden | Schadensdarstellungen |

### Symbol-Suche

- **Echtzeit-Filterung** beim Tippen
- **Mehrsprachig** — Deutsch und Englisch
- **Kategorien-Filter** — Schnelle Navigation durch Kategorien

### Datenverwaltung

- **Automatisches Speichern** — Änderungen werden sofort gespeichert
- **Projekt-Integration** — Layer-Dateien werden beim Speichern des Projekts verschoben
- **Portable Pakete** — Export-Funktion für vollständig portable Symbol-Sammlungen
- **SVG-Embedding** — SVG-Inhalte werden in der GeoPackage eingebettet für maximale Portabilität

---

## Installation

### Voraussetzungen

- **QGIS** 3.0 oder höher
- **Python** 3.x (wird mit QGIS mitgeliefert)
- **Betriebssystem** — Windows, Linux oder macOS

### Option 1: QGIS Plugin-Manager (empfohlen)

1. QGIS öffnen
2. `Plugins` > `Verwalten und installieren`
3. Tab "Installiert" > "THW Toolbox" aktivieren

### Option 2: Manuelle Installation

1. Plugin herunterladen oder Repository klonen
2. Plugin-Ordner ins QGIS Plugin-Verzeichnis kopieren:
   - **Windows**: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
3. QGIS neu starten

---

## Verwendung

### Plugin aktivieren

1. Klicken Sie auf das **THW Toolbox-Symbol** in der QGIS-Toolbar
2. Das **Symbol-Dock** öffnet sich rechts in QGIS
3. Ein neuer Layer **"THW Toolbox Marker"** wird automatisch erstellt

### Symbole platzieren

1. Im Symbol-Dock zur gewünschten Kategorie navigieren
2. Symbol aus dem Dock ziehen
3. Auf der gewünschten Position auf der Karte loslassen

### Symbole bearbeiten

**Auswählen** — Klick auf ein Symbol auf der Karte öffnet das Feature-Dock mit allen Eigenschaften

**Verschieben** — Linke Maustaste gedrückt halten und an die neue Position ziehen

**Größe ändern** — Größen-Schieberegler im Feature-Dock verwenden

**Label bearbeiten** — "Label anzeigen" aktivieren und Text eingeben

### Symbol-Suche

Suchleiste im oberen Bereich des Symbol-Docks nutzen. Funktioniert mit deutschen und englischen Begriffen. Klick auf **X** setzt die Suche zurück.

---

## Technische Details

### Datenformat

| Eigenschaft | Wert |
|-------------|------|
| Layer-Typ | GeoPackage (.gpkg) |
| Geometrie | Punkt-Features |
| Symbole | SVG mit eingebettetem Inhalt |
| CRS | Automatische Anpassung an Projekt-CRS |
| Speicherort | `tmp`-Verzeichnis des Plugins |

### Feature-Attribute

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `name` | String | Name der SVG-Datei |
| `svg_path` | String | Relativer Pfad zur SVG-Datei |
| `svg_content` | String | Vollständiger SVG-Inhalt (Portabilität) |
| `size` | Real | Symbolgröße in Map Units |
| `scale_with_map` | Boolean | Skalierung mit der Karte |
| `unique_id` | String | Eindeutige Feature-ID |
| `label` | String | Beschriftungstext |
| `show_label` | Boolean | Beschriftung anzeigen |

### Performance

- **Intelligente Toleranz** — Feature-Erkennung basiert auf Symbolgröße
- **Throttling** — Aktualisierungen werden gedrosselt
- **Caching** — SVG-Icons werden gecacht
- **Lazy Loading** — Ordner werden nur bei Bedarf geladen

---

## Export

### Portables Paket erstellen

1. `Plugins` > `THW Toolbox` > `Portables Paket exportieren`
2. Zielordner auswählen
3. Das Plugin erstellt ein ZIP-Archiv mit allen SVG-Symbolen, der GeoPackage-Datei und Installationsanweisungen

Das Paket kann auf anderen Systemen ohne zusätzliche Abhängigkeiten verwendet werden.

---

## Credits

### Taktische Zeichen

Die taktischen Zeichen stammen aus dem hervorragenden Projekt [Taktische-Zeichen](https://github.com/jonas-koeritz/Taktische-Zeichen) von **[Jonas Köritz](https://github.com/jonas-koeritz)**. Vielen Dank für die umfangreiche und hochwertige Sammlung von über 1000 taktischen Zeichen, die als SVG frei zur Verfügung gestellt werden.

Ein besonderer Dank geht auch an **[ZeiberKreim](https://github.com/ZeiberKreim)** für wertvolle Beiträge und Erweiterungen der Zeichen-Sammlung.

### Verwendete Ressourcen

| Ressource | Lizenz | Quelle |
|-----------|--------|--------|
| Taktische Zeichen | CC BY 4.0 | [jonas-koeritz/Taktische-Zeichen](https://github.com/jonas-koeritz/Taktische-Zeichen) |
| Google Roboto Font | Apache 2.0 | [Google Fonts](https://fonts.google.com/specimen/Roboto) |

---

## Lizenz

Dieses Plugin steht unter der **MIT-Lizenz**. Siehe `LICENSE` für Details.

---

## Support und Fehlerbehebung

### Häufige Probleme

**Plugin erscheint nicht in der Toolbar**
- Plugin im Plugin-Manager aktiviert?
- QGIS neu starten
- QGIS-Version mindestens 3.0?

**Symbole werden nicht angezeigt**
- Layer "THW Toolbox Marker" sichtbar?
- SVG-Dateien im `svgs`-Verzeichnis vorhanden?
- Schreibrechte im Plugin-Verzeichnis?

**Performance-Probleme**
- Anzahl gleichzeitig angezeigter Symbole reduzieren
- Größe der SVG-Dateien prüfen
- Andere große Projekte oder Layer schließen

### Logs

- **Log-Datei**: `svg_dock.log` im Plugin-Verzeichnis
- **QGIS-Log**: `Plugins` > `Python-Konsole` > Log-Ausgabe

### Bekannte Einschränkungen

- Nur Punkt-Layer unterstützt
- Sehr große SVGs (> 1 MB) können die Performance beeinträchtigen
- Bei > 1000 Symbolen kann die Darstellung verlangsamt werden
- Symbol-Suche ist case-sensitive

---

## Beitragen

Verbesserungsvorschläge, Bug-Reports und Pull Requests sind herzlich willkommen!

- **Bug-Reports** — Fehler über GitHub Issues melden
- **Feature-Vorschläge** — Ideen teilen
- **Code-Beiträge** — Pull Requests sind willkommen

---

**Hinweis**: Dieses Plugin wurde für den Einsatz im THW entwickelt, kann aber auch für andere Organisationen verwendet werden. Die taktischen Zeichen entsprechen den offiziellen Standards.
