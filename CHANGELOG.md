# Changelog

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

