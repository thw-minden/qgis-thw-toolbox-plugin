#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
THW Toolbox Plugin - Export Script
==================================

Dieses Script exportiert das THW Toolbox Plugin als portables Paket.
Es kann sowohl innerhalb als auch außerhalb von QGIS ausgeführt werden.

Verwendung:
    python export_script.py [Zielverzeichnis]

Beispiele:
    python export_script.py
    python export_script.py "C:\\Users\\Benutzer\\Desktop\\Export"
    python export_script.py /home/user/Desktop/Export
"""

import os
import sys
import shutil
import zipfile
import argparse
from pathlib import Path


class THWPluginExporter:
    """Klasse zum Exportieren des THW Toolbox Plugins."""
    
    def __init__(self, plugin_dir=None):
        """
        Initialisiert den Exporter.
        
        Args:
            plugin_dir (str, optional): Pfad zum Plugin-Verzeichnis. 
                                      Wenn None, wird das aktuelle Verzeichnis verwendet.
        """
        if plugin_dir is None:
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.plugin_dir = Path(plugin_dir)
        self.required_files = [
            "thwtoolboxplugin.py",
            "thwtoolboxplugin_dock.py", 
            "identifytool.py",
            "dock_manager.py",
            "dragmaptool.py",
            "layer_manager.py",
            "mapcanvas_dropevent_filter.py",
            "__init__.py",
            "metadata.txt"
        ]
        
        self.required_dirs = [
            "svgs",
            "icons"
        ]
    
    def validate_plugin_structure(self):
        """
        Validiert, ob alle erforderlichen Dateien und Verzeichnisse vorhanden sind.
        
        Returns:
            tuple: (is_valid, missing_files, missing_dirs)
        """
        missing_files = []
        missing_dirs = []
        
        # Prüfe erforderliche Dateien
        for file_name in self.required_files:
            file_path = self.plugin_dir / file_name
            if not file_path.exists():
                missing_files.append(file_name)
        
        # Prüfe erforderliche Verzeichnisse
        for dir_name in self.required_dirs:
            dir_path = self.plugin_dir / dir_name
            if not dir_path.exists():
                missing_dirs.append(dir_name)
        
        is_valid = len(missing_files) == 0 and len(missing_dirs) == 0
        
        return is_valid, missing_files, missing_dirs
    
    def export_portable_package(self, export_path, include_gpkg=True):
        """
        Exportiert das Plugin als portables Paket.
        
        Args:
            export_path (str): Pfad, wo das portable Paket gespeichert werden soll
            include_gpkg (bool): Ob GeoPackage-Dateien mit exportiert werden sollen
            
        Returns:
            bool: True wenn erfolgreich, False bei Fehlern
        """
        try:
            export_path = Path(export_path)
            
            # Validiere Plugin-Struktur
            is_valid, missing_files, missing_dirs = self.validate_plugin_structure()
            if not is_valid:
                print("FEHLER: Plugin-Struktur ist unvollständig!")
                if missing_files:
                    print(f"   Fehlende Dateien: {', '.join(missing_files)}")
                if missing_dirs:
                    print(f"   Fehlende Verzeichnisse: {', '.join(missing_dirs)}")
                return False
            
            print(f"[EXPORT] Exportiere Plugin von: {self.plugin_dir}")
            print(f"[EXPORT] Zielverzeichnis: {export_path}")
            
            # Erstelle Export-Verzeichnis
            export_path.mkdir(parents=True, exist_ok=True)
            
            # Kopiere Python-Dateien
            print("Kopiere Python-Dateien...")
            for py_file in self.required_files:
                source = self.plugin_dir / py_file
                if source.exists():
                    shutil.copy2(source, export_path)
                    print(f"   [OK] {py_file}")
                else:
                    print(f"   [WARN] {py_file} nicht gefunden")
            
            # Kopiere SVG-Verzeichnis
            svg_source = self.plugin_dir / "svgs"
            svg_dest = export_path / "svgs"
            if svg_source.exists():
                print("Kopiere SVG-Symbole...")
                shutil.copytree(svg_source, svg_dest, dirs_exist_ok=True)
                svg_count = sum(1 for _ in svg_dest.rglob("*.svg"))
                print(f"   [OK] {svg_count} SVG-Dateien kopiert")
            else:
                print("   [WARN] SVG-Verzeichnis nicht gefunden")
            
            # Kopiere Icons-Verzeichnis
            icon_source = self.plugin_dir / "icons"
            icon_dest = export_path / "icons"
            if icon_source.exists():
                print("Kopiere Icons...")
                shutil.copytree(icon_source, icon_dest, dirs_exist_ok=True)
                icon_count = sum(1 for _ in icon_dest.rglob("*"))
                print(f"   [OK] {icon_count} Icon-Dateien kopiert")
            else:
                print("   [WARN] Icons-Verzeichnis nicht gefunden")
            
            # Kopiere GeoPackage-Dateien (optional)
            if include_gpkg:
                print("Suche nach GeoPackage-Dateien...")
                gpkg_files = list(self.plugin_dir.glob("*.gpkg"))
                if gpkg_files:
                    for gpkg_file in gpkg_files:
                        dest_gpkg = export_path / gpkg_file.name
                        shutil.copy2(gpkg_file, dest_gpkg)
                        print(f"   [OK] {gpkg_file.name} kopiert")
                else:
                    print("   [INFO] Keine GeoPackage-Dateien gefunden")
            
            # Erstelle README-Datei
            print("Erstelle README-Datei...")
            readme_content = self._create_readme_content()
            readme_path = export_path / "README.txt"
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(readme_content)
            print("   [OK] README.txt erstellt")
            
            # Erstelle ZIP-Archiv
            print("Erstelle ZIP-Archiv...")
            zip_path = export_path.with_suffix('.zip')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(export_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(export_path)
                        zipf.write(file_path, arcname)
            
            zip_size = zip_path.stat().st_size / (1024 * 1024)  # MB
            print(f"   [OK] ZIP-Archiv erstellt: {zip_path.name} ({zip_size:.1f} MB)")
            
            print("\n[SUCCESS] Export erfolgreich abgeschlossen!")
            print(f"[EXPORT] Portables Paket: {zip_path}")
            print(f"[EXPORT] Entpacktes Verzeichnis: {export_path}")
            
            return True
            
        except Exception as e:
            print(f"\n[ERROR] Fehler beim Export: {str(e)}")
            return False
    
    def _create_readme_content(self):
        """Erstellt den Inhalt der README-Datei."""
        return """THW Toolbox Plugin - Portables Paket
=============================================

Installation:
1. Entpacken Sie alle Dateien in einen Ordner
2. Kopieren Sie den gesamten Ordner in Ihr QGIS Plugin-Verzeichnis:
   - Windows: %APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\
   - Linux: ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
   - macOS: ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
3. Aktivieren Sie das Plugin in QGIS über Plugins -> Verwalten und installieren

Verwendung:
- Das Plugin erstellt automatisch einen Layer "THW Toolbox Marker"
- Alle Symbole werden in der GeoPackage-Datei gespeichert
- Die Symbole sind vollständig portabel und funktionieren auch ohne das Plugin

Features:
- Über 1000 SVG-Symbole für verschiedene Einsatzorganisationen
- Drag & Drop Symbol-Platzierung
- Intelligente Größenanpassung
- Feature-Identifizierung und -Bearbeitung
- Symbol-Verschiebung
- Dynamische Beschriftung
- Persistente Speicherung in GeoPackage-Format

Organisationen:
- THW (Technisches Hilfswerk)
- Bundeswehr
- Feuerwehr
- Polizei
- Rettungswesen
- Katastrophenschutz
- Wasserrettung
- Zoll

Hinweise:
- Alle SVG-Symbole sind im 'svgs' Ordner enthalten
- Die GeoPackage-Datei enthält alle gesetzten Symbole mit Koordinaten
- Das Plugin ist vollständig portabel und benötigt keine externen Abhängigkeiten

Lizenz:
- Taktische Zeichen: CC BY 4.0 (Creative Commons Attribution 4.0 International)
- Google Roboto Font: Apache 2.0 (Apache License, Version 2.0)

Weitere Informationen:
- GitHub: https://github.com/thw-minden/qgis-thw-toolbox-plugin
- Issues: https://github.com/thw-minden/qgis-thw-toolbox-plugin/issues
- E-Mail: code@thw-minden.de

Erstellt mit dem THW Toolbox Export Script
"""


def main():
    """Hauptfunktion des Export-Scripts."""
    parser = argparse.ArgumentParser(
        description="Exportiert das THW Toolbox Plugin als portables Paket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python export_script.py
  python export_script.py "C:\\Users\\Benutzer\\Desktop\\Export"
  python export_script.py /home/user/Desktop/Export
  python export_script.py --no-gpkg /tmp/thw_export
        """
    )
    
    parser.add_argument(
        'export_path',
        nargs='?',
        default=None,
        help='Zielverzeichnis für den Export (Standard: Desktop/THW_Toolbox_Portable)'
    )
    
    parser.add_argument(
        '--no-gpkg',
        action='store_true',
        help='GeoPackage-Dateien nicht mit exportieren'
    )
    
    parser.add_argument(
        '--plugin-dir',
        help='Pfad zum Plugin-Verzeichnis (Standard: aktuelles Verzeichnis)'
    )
    
    args = parser.parse_args()
    
    # Bestimme Export-Pfad
    if args.export_path is None:
        desktop = Path.home() / "Desktop"
        export_path = desktop / "THW_Toolbox_Portable"
    else:
        export_path = Path(args.export_path)
    
    # Bestimme Plugin-Verzeichnis
    plugin_dir = args.plugin_dir if args.plugin_dir else None
    
    print("THW Toolbox Plugin Export Script")
    print("=" * 50)
    
    # Erstelle Exporter
    exporter = THWPluginExporter(plugin_dir)
    
    # Führe Export durch
    success = exporter.export_portable_package(
        export_path, 
        include_gpkg=not args.no_gpkg
    )
    
    if success:
        print(f"\n[SUCCESS] Export erfolgreich abgeschlossen!")
        print(f"[EXPORT] ZIP-Datei: {export_path.with_suffix('.zip')}")
        print(f"[EXPORT] Verzeichnis: {export_path}")
        sys.exit(0)
    else:
        print(f"\n[ERROR] Export fehlgeschlagen!")
        sys.exit(1)


if __name__ == "__main__":
    main()
