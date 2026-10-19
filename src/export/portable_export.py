import os
import platform
import shutil
import subprocess  # nosec B404 — only used for OS-standard "reveal in file manager" calls
import zipfile
from collections.abc import Callable

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtWidgets import QFileDialog

from ..logging_utils import get_logger

logger = get_logger(__name__)

# Files / dirs that ship with every export. Resources stay at plugin root,
# Python code lives under src/, plus the QGIS plugin manifest. data/ holds the
# Objektplanung catalog (tents, vehicles, ...) and the THW-Dienststellen list.
_EXPORT_DIRS = ("svgs", "icons", "src", "templates", "data")
_EXPORT_FILES = ("__init__.py", "metadata.txt", "LICENSE")
_GPKG_EXPORT_NAME = "taktische_zeichen.gpkg"
# Subdirectory created inside the user-picked container
_BUNDLE_DIR_NAME = "THW_Toolbox_Portable"
# Top-level folder inside the ZIP. QGIS' "Install from ZIP" requires exactly
# one top-level directory containing metadata.txt, so everything nests here.
_PLUGIN_FOLDER_NAME = "qgisthwplugin"

_README = """THW Toolbox Plugin - Portables Paket

Installation (empfohlen, via QGIS):
1. In QGIS: Plugins -> Verwalten und installieren -> Aus ZIP installieren
2. Die ZIP-Datei auswählen und auf "Plugin installieren" klicken

Installation (manuell):
1. ZIP entpacken
2. Den Ordner "qgisthwplugin" in Ihr QGIS Plugin-Verzeichnis kopieren:
   - Windows: %APPDATA%/QGIS/QGIS3/profiles/default/python/plugins/
   - Linux: ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
   - macOS: ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
3. Plugin in QGIS über Plugins -> Verwalten und installieren aktivieren

Verwendung:
- Das Plugin erstellt automatisch einen Layer "THW Toolbox Marker"
- Alle Symbole werden in der GeoPackage-Datei gespeichert
- Die Symbole sind vollständig portabel und funktionieren auch ohne das Plugin

Hinweis:
- Alle SVG-Symbole sind im 'svgs' Ordner enthalten
- Die mitgelieferte GeoPackage-Datei enthält alle gesetzten Symbole mit Koordinaten
"""


class PortableExporter:
    """Bundle the plugin + current GeoPackage into a portable ZIP.

    Copies plugin code (src/), resources (svgs/, icons/, templates/, data/), the current
    layer's GeoPackage, plus a README into a target directory and zips it.
    The output ZIP can be dropped into any QGIS plugin folder.

    UI feedback (success message, error dialog) goes through callbacks
    so this module stays free of QGIS iface coupling.
    """

    def __init__(
        self,
        plugin_dir: str,
        get_layer: Callable[[], QgsVectorLayer | None],
        on_success: Callable[[str], None],
        on_error: Callable[[str, str, str], None],
    ):
        self._plugin_dir = plugin_dir
        self._get_layer = get_layer
        self._on_success = on_success
        self._on_error = on_error

    def export(self, target_dir: str) -> bool:
        """Build the bundle at `target_dir`, zip it, then remove the staging dir."""
        try:
            os.makedirs(target_dir, exist_ok=True)
            self._copy_resources(target_dir)
            self._copy_geopackage(target_dir)
            self._write_readme(target_dir)
            zip_path = self._make_zip(target_dir)
            # Staging dir was only needed to build the zip — drop it so only
            # the .zip remains for the user.
            shutil.rmtree(target_dir, ignore_errors=True)
            self._on_success(zip_path)
            return True
        except Exception as e:
            self._on_error(
                "Export-Fehler",
                "Konnte portables Paket nicht erstellen",
                f"Export-Pfad: {target_dir}\nFehler: {e}",
            )
            return False

    def prompt_and_export(self, parent_widget) -> bool:
        """Ask the user for a container dir, then export into a subfolder inside it."""
        # Seed the dialog with the user's Desktop (which exists). The bundle
        # subfolder gets appended after — getExistingDirectory only accepts
        # existing paths as the initial location.
        initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")

        container = QFileDialog.getExistingDirectory(
            parent_widget, "Übergeordnetes Verzeichnis für portables Paket auswählen", initial_dir
        )
        if not container:
            return False

        target_dir = os.path.join(container, _BUNDLE_DIR_NAME)
        if not self.export(target_dir):
            return False
        # Reveal the .zip in the file manager (staging dir was already deleted)
        _reveal_in_file_manager(os.path.normpath(target_dir + ".zip"))
        return True

    def _copy_resources(self, target_dir: str) -> None:
        plugin_out = os.path.join(target_dir, _PLUGIN_FOLDER_NAME)
        os.makedirs(plugin_out, exist_ok=True)
        # QGIS' "Install from ZIP" rejects archives containing .pyc files,
        # so strip bytecode caches during the copy.
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
        for name in _EXPORT_DIRS:
            src = os.path.join(self._plugin_dir, name)
            if os.path.exists(src):
                shutil.copytree(src, os.path.join(plugin_out, name), dirs_exist_ok=True, ignore=ignore)
        for name in _EXPORT_FILES:
            src = os.path.join(self._plugin_dir, name)
            if os.path.exists(src):
                shutil.copy2(src, plugin_out)

    def _copy_geopackage(self, target_dir: str) -> None:
        layer = self._get_layer()
        if not layer or layer.providerType() != "ogr":
            return
        source_gpkg = layer.source().split("|")[0]
        if os.path.exists(source_gpkg):
            dest = os.path.join(target_dir, _PLUGIN_FOLDER_NAME, _GPKG_EXPORT_NAME)
            shutil.copy2(source_gpkg, dest)

    def _write_readme(self, target_dir: str) -> None:
        readme_path = os.path.join(target_dir, _PLUGIN_FOLDER_NAME, "README.txt")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(_README)

    def _make_zip(self, target_dir: str) -> str:
        zip_path = target_dir + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _dirs, files in os.walk(target_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, os.path.relpath(file_path, target_dir))
        return zip_path


def _reveal_in_file_manager(file_path: str) -> None:
    """Open the file manager and select `file_path`. Failures are silent.

    Windows/macOS support reveal-and-select; Linux's xdg-open only opens
    folders, so we fall back to opening the parent directory.
    """
    # OS-Standard-Befehle; file_path stammt aus der eigenen Export-Logik, nicht aus User-Input.
    try:
        system = platform.system()
        if system == "Windows":
            # /select,<path> — comma, no space; explorer is picky about quoting
            subprocess.run(["explorer", f"/select,{file_path}"])  # nosec B603 B607
        elif system == "Darwin":
            subprocess.run(["open", "-R", file_path])  # nosec B603 B607
        else:
            subprocess.run(["xdg-open", os.path.dirname(file_path)])  # nosec B603 B607
    except Exception as e:
        logger.debug("Reveal in file manager failed for %s: %s", file_path, e)
