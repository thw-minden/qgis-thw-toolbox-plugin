import os

from qgis.core import QgsApplication


def plugin_root() -> str:
    """Absolute path to the plugin root directory (parent of src/).

    Used to locate resources that live outside the Python package: icons/,
    svgs/, templates/, data/, metadata.txt. All modules should call this helper
    instead of computing dirname(__file__) themselves, since the depth
    relative to the plugin root depends on the module's location in src/.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def profile_dir() -> str:
    """Ordner des Plugins im QGIS-Profil (``thw_toolbox``).

    Alles, was das Plugin selbst anlegt, gehört hierher und nicht in den Plugin-Ordner:
    Den löscht QGIS bei jedem Update über den Plugin-Manager.
    """
    return os.path.normpath(os.path.join(QgsApplication.qgisSettingsDirPath(), "thw_toolbox"))


def temp_dir() -> str:
    """GeoPackages ungespeicherter Projekte; beim Speichern ziehen sie neben die Projektdatei."""
    return os.path.join(profile_dir(), "tmp")


def legacy_temp_dir() -> str:
    """``tmp/`` im Plugin-Ordner, in dem Versionen bis 2.1.1 diese GeoPackages ablegten."""
    return os.path.join(plugin_root(), "tmp")


def in_temp_dir(path: str) -> bool:
    """Liegt ``path`` in einem der beiden tmp-Ordner (Profil oder, von älteren Versionen, Plugin-Ordner)?"""
    folder = os.path.normcase(os.path.abspath(os.path.dirname(path)))
    return any(folder == os.path.normcase(os.path.abspath(d)) for d in (temp_dir(), legacy_temp_dir()))
