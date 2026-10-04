# THW Toolbox Plugin

![Plugin-Vorschau](docs/preview.gif)

Ein QGIS-Plugin für das einfache Hinzufügen und Verwalten von taktischen Zeichen auf der Karte. Entwickelt speziell für den Einsatz im Technischen Hilfswerk (THW) und anderen Einsatzorganisationen.

---

## Schnellstart

1. **Plugin aktivieren** - Klicken Sie auf das THW Toolbox-Symbol in der QGIS-Toolbar
2. **Symbol auswählen** - Wählen Sie ein Symbol aus dem Dock aus
3. **Platzieren** - Ziehen Sie das Symbol auf die Karte oder klicken Sie auf die gewünschte Position
4. **Bearbeiten** - Klicken Sie auf ein Symbol, um es zu verschieben, zu skalieren oder zu beschriften

---

## Hauptfunktionen

### Symbol-Platzierung

- **Drag & Drop** - Symbole direkt aus dem Dock auf die Karte ziehen
- **Klick-Modus** - Symbol auswählen und auf die gewünschte Position klicken
- **Intelligente Größenanpassung** - Symbole werden automatisch an den Zoom-Faktor angepasst
- **Persistente Speicherung** - Alle Symbole werden automatisch in einer GeoPackage-Datei gespeichert

### Feature-Management

- **Identifizierung** - Klick auf Symbole zeigt Details an
- **Verschieben** - Symbole per Maus an neue Position ziehen
- **Größenanpassung** - Dynamische Größenänderung mit Schieberegler
- **Labeling** - Beschriftung mit anpassbarem Text und Positionierung
- **Koordinaten** - MGRS-Koordinate des Symbols in den Marker-Details, mit Kopier-Button
- **Echtzeit-Vorschau** - Sofortige visuelle Rückmeldung bei Änderungen

### Annotationen

Eigene Werkzeugleiste **„THW Toolbox Annotationen“** für Lagekarten-Eintragungen. Annotationen liegen im Layer „THW Toolbox Annotationen“ und werden mit der Projektdatei gespeichert.

#### Zeichnen

| Werkzeug | Bedienung |
|----------|-----------|
| **Text setzen** | Klick auf die Karte, Text eingeben (mehrzeilig); der Text steht mittig über dem Klickpunkt |
| **Punkt setzen** | Klick auf die Karte; optional Beschreibung und Radius in Metern |
| **Linie zeichnen** | Linksklick = Stützpunkt, Rechtsklick/Enter = fertig, Rücktaste = letzten Punkt entfernen, Esc = abbrechen |
| **Polygon zeichnen** | wie Linie; wahlweise nur Umriss oder gefüllt |

Beim Zeichnen von Linien und Polygonen zeigt ein Hinweis am Mauszeiger die Länge des aktuellen Segments sowie die Gesamtlänge bzw. Umfang und Fläche.

#### Auf der Karte bearbeiten (Verschieben-Werkzeug)

| Aktion | Wirkung |
|--------|---------|
| Punkt/Stützpunkt ziehen | verschieben (aktuelle MGRS-Koordinate am Mauszeiger) |
| Schwerpunkt ⊕ ziehen | ganze Linie bzw. Fläche samt Beschreibung verschieben |
| Linksklick auf Linie/Umriss | Stützpunkt einfügen |
| Rechtsklick auf Stützpunkt | Stützpunkt entfernen (Linien behalten mind. 2, Polygone mind. 3) |
| Text, Beschreibung oder Längenangabe ziehen | verschieben |
| Ecke des Textrahmens ziehen | Textgröße ändern |
| knapp außerhalb einer Rahmenecke ziehen | Text drehen (Umschalt = 15°-Schritte) |
| Doppelklick auf Punkt, Text, Beschreibung oder ⊕ | Bearbeiten-Dialog öffnen |
| Esc | laufende Aktion abbrechen |

#### Bearbeiten-Dialog

Öffnet per Klick in der Dock-Liste oder per Doppelklick auf der Karte.

- **Beschreibung / Text** - mehrzeilig (Enter = neue Zeile, Strg+Enter bestätigt)
- **Darstellung** - Farbe inkl. Transparenz, Linienbreite, Füllung
- **Punkte** - Radius in Metern als maßstabsgetreuer Kreis (z. B. Gefahren- oder Sperrbereich) mit eigener Füllfarbe
- **Linien** - Pfeilspitzen am Anfang, Ende oder beiden Enden mit einstellbarer Größe
- **Maße auf der Karte** - Radius bzw. Segment-/Kantenlängen einblenden, Textgröße einstellbar
- **Messwerte** - Länge, Umfang und Fläche
- **Koordinaten (MGRS)** - einklappbare Liste aller Punkte; Doppelklick kopiert eine Koordinate; im Bearbeiten-Modus Koordinaten eingeben sowie Punkte einfügen oder löschen
- **Löschen** des Objekts

#### Koordinaten

- **`$POS`** in einer Punktbeschreibung oder einem Text wird auf der Karte durch die MGRS-/UTMRef-Koordinate ersetzt, z. B. `32U MB 12345 98765`, und beim Verschieben aktualisiert
- Die Auflösung (0,1 m, 1 m, 10 m oder 100 m) ist im Einstellungs-Dialog projektweit einstellbar

#### Dock-Tab „Annotationen“ und Export

- **Liste** aller Objekte mit Vorschau-Symbol; Klick öffnet den Bearbeiten-Dialog
- **KMZ-Export** - Export-Symbol in der Zeile eines Polygons; Name und Farbe bleiben erhalten (drohnentauglich, z. B. DJI Pilot 2)
- **MBTiles-Export** - Button unten im Tab erzeugt eine Kachel-Ebene mit allen Annotationen (Zoomstufen wählbar)

#### Rückgängig / Wiederherstellen

**Strg+Z** macht Annotations-Änderungen der laufenden Sitzung rückgängig, **Strg+Y** (oder Strg+Umschalt+Z) stellt sie wieder her. Voraussetzung: Karte oder THW-Dock haben den Fokus und kein Vektorlayer ist im Bearbeitungsmodus (dann gilt das Strg+Z von QGIS).

### Adress- und Koordinatensuche

- **Alt+S** öffnet die Suche nach Adressen und Orten (OpenStreetMap / Nominatim)
- Eine eingegebene **MGRS-Koordinate** (z. B. `32U MB 12345 98765`) wird direkt angesprungen und kurz hervorgehoben

### Einstellungen

- **Einstellungs-Dialog** - Zentrale Konfiguration über das Zahnrad-Icon im Dock
- **Icon-Standardwerte** - Standardgröße und Kartenskalierung konfigurierbar
- **Label-Konfiguration** - Schriftgröße, Buffer-Größe und Labels ein-/ausschalten
- **Annotationen** - Standard-Linien- und Füllfarbe (inkl. Transparenz) und Standard-Linienbreite
- **MGRS-Auflösung** - Genauigkeit der Koordinaten für `$POS`, Koordinaten-Tabellen und Marker-Details

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
- **Mehrsprachig** - Deutsch und Englisch
- **Kategorien-Filter** - Schnelle Navigation durch Kategorien

### Datenverwaltung

- **Automatisches Speichern** - Änderungen werden sofort gespeichert
- **Projekt-Integration** - Layer-Dateien werden beim Speichern des Projekts verschoben
- **Portable Pakete** - Export-Funktion für vollständig portable Symbol-Sammlungen
- **SVG-Embedding** - SVG-Inhalte werden in der GeoPackage eingebettet für maximale Portabilität

---

## Installation

### Voraussetzungen

- **QGIS** 3.44 oder höher
- **Python** 3.x (wird mit QGIS mitgeliefert)
- **Betriebssystem** - Windows, Linux oder macOS

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

**Auswählen** - Klick auf ein Symbol auf der Karte öffnet das Feature-Dock mit allen Eigenschaften

**Verschieben** - Linke Maustaste gedrückt halten und an die neue Position ziehen

**Größe ändern** - Größen-Schieberegler im Feature-Dock verwenden

**Label bearbeiten** - "Label anzeigen" aktivieren und Text eingeben

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

- **Intelligente Toleranz** - Feature-Erkennung basiert auf Symbolgröße
- **Throttling** - Aktualisierungen werden gedrosselt
- **Caching** - SVG-Icons werden gecacht
- **Lazy Loading** - Ordner werden nur bei Bedarf geladen

---

## Export

### Portables Paket erstellen

1. `Plugins` > `THW Toolbox` > `Portables Paket exportieren`
2. Zielordner auswählen
3. Das Plugin erstellt ein ZIP-Archiv mit allen SVG-Symbolen, der GeoPackage-Datei und Installationsanweisungen

Das Paket kann auf anderen Systemen ohne zusätzliche Abhängigkeiten verwendet werden.

---

## Credits

Die taktischen Zeichen stammen aus dem hervorragenden Projekt [Taktische-Zeichen](https://github.com/jonas-koeritz/Taktische-Zeichen) von **[Jonas Köritz](https://github.com/jonas-koeritz)**. Vielen Dank für die umfangreiche und hochwertige Sammlung von über 1000 taktischen Zeichen, die als SVG frei zur Verfügung gestellt werden.

Ein besonderer Dank geht auch an **[ZeiberKreim](https://github.com/ZeiberKreim)** für wertvolle Beiträge und Erweiterungen des Plugins.

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
- QGIS-Version mindestens 3.44?

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

## Entwicklung

### Dev-Abhängigkeiten installieren

```powershell
pip install -r requirements-dev.txt
```

### Security-Scan (Bandit)

Sucht nach typischen Python-Sicherheitsproblemen (SQL-Injection, unsichere Funktionen, hardcodierte Passwörter usw.):

```powershell
bandit -r src
```

Nützliche Flags:

- `-ll` — nur Medium+ Severity zeigen
- `-f json -o bandit.json` — Report als JSON speichern

False-Positives lassen sich mit `# nosec <BXXX>` an der betroffenen Zeile unterdrücken (immer mit kurzem Kommentar, warum es sicher ist).

### Lint & Format (Ruff)

```powershell
ruff check --fix .
ruff format .
```

Läuft auch automatisch über den Pre-Commit-Hook (`.pre-commit-config.yaml`).

---

## Beitragen

Verbesserungsvorschläge, Bug-Reports und Pull Requests sind herzlich willkommen!

- **Bug-Reports** - Fehler über GitHub Issues melden
- **Feature-Vorschläge** - Ideen teilen
- **Code-Beiträge** - Pull Requests sind willkommen

---

**Hinweis**: Dieses Plugin wurde für den Einsatz im THW entwickelt, kann aber auch für andere Organisationen verwendet werden. Die taktischen Zeichen entsprechen den offiziellen Standards.
