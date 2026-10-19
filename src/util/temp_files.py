import glob
import os
import shutil
import time

from ..logging_utils import get_logger

logger = get_logger(__name__)

# Alte GeoPackage-Snapshots im tmp/ älter als 24h löschen
_GPKG_AGE_THRESHOLD_S = 24 * 60 * 60
# Reste des SVG-/Preview-Caches älter als 1h löschen. Frühere Versionen legten dort
# Zeichen und Vorschauen als Dateien ab; heute stecken sie im Symbol bzw. im Speicher.
_CACHE_AGE_THRESHOLD_S = 60 * 60


def cleanup_temp_files(plugin_dir: str) -> None:
    """Räume Plugin-Temp-Verzeichnisse auf (idempotent, fail-safe pro Pfad).

    Wird beim Plugin-Activate und -Unload aufgerufen, damit alte Snapshots und
    Cache-Reste früherer Versionen nicht ewig im Plugin-Verzeichnis liegen bleiben.
    Einzelne Fehler (z.B. Datei noch von QGIS gelockt) werden geschluckt
    und als DEBUG-Print ausgegeben — der Cleanup ist Best-Effort.
    """
    try:
        # 1) GeoPackage-Snapshots im tmp/ + Plugin-Root (Legacy)
        tmp_dir = os.path.join(plugin_dir, "tmp")
        if os.path.exists(tmp_dir):
            temp_files = glob.glob(os.path.join(tmp_dir, "*_taktischezeichen.gpkg"))
        else:
            temp_files = []
        # Plugin-Root war früher Speicherort — Rückwärtskompatibilität
        temp_files.extend(glob.glob(os.path.join(plugin_dir, "*_taktischezeichen.gpkg")))

        current_time = time.time()
        for temp_file in temp_files:
            try:
                if current_time - os.path.getmtime(temp_file) > _GPKG_AGE_THRESHOLD_S:
                    try:
                        os.remove(temp_file)
                        logger.debug("Temporäre GeoPackage-Datei gelöscht: %s", temp_file)
                    except PermissionError:
                        logger.debug("Temporäre GeoPackage-Datei noch gesperrt, überspringe: %s", temp_file)
            except Exception as e:
                logger.debug("Konnte temporäre GeoPackage-Datei nicht verarbeiten %s: %s", temp_file, e)

        # 2) Reste des SVG-/Preview-Caches früherer Versionen
        cache_dirs = [
            os.path.join(plugin_dir, "temp_files", "svg_cache"),
            os.path.join(plugin_dir, "temp_files", "preview_cache"),
            os.path.join(plugin_dir, "temp_svg"),  # Legacy-Pfad
        ]
        for cache_dir in cache_dirs:
            if not os.path.exists(cache_dir):
                continue
            try:
                for filename in os.listdir(cache_dir):
                    file_path = os.path.join(cache_dir, filename)
                    if not os.path.isfile(file_path):
                        continue
                    if current_time - os.path.getmtime(file_path) > _CACHE_AGE_THRESHOLD_S:
                        try:
                            os.remove(file_path)
                            logger.debug("Temporäre Cache-Datei gelöscht: %s", file_path)
                        except PermissionError:
                            logger.debug("Cache-Datei noch gesperrt, überspringe: %s", file_path)
            except Exception as e:
                logger.warning("Fehler beim Bereinigen des Cache-Verzeichnisses %s: %s", cache_dir, e)

        # 3) Komplett leeres temp_files/ entfernen
        temp_files_dir = os.path.join(plugin_dir, "temp_files")
        if os.path.exists(temp_files_dir):
            try:
                all_empty = True
                for _root, _dirs, files in os.walk(temp_files_dir):
                    if files:
                        all_empty = False
                        break
                if all_empty:
                    shutil.rmtree(temp_files_dir)
                    logger.debug("Leeres temp_files Verzeichnis entfernt: %s", temp_files_dir)
            except Exception as e:
                logger.warning("Fehler beim Entfernen des temp_files Verzeichnisses: %s", e)

        # 4) Leeres tmp/ entfernen
        if os.path.exists(tmp_dir):
            try:
                if not os.listdir(tmp_dir):
                    os.rmdir(tmp_dir)
                    logger.debug("Leerer tmp-Ordner entfernt: %s", tmp_dir)
            except Exception as e:
                logger.warning("Fehler beim Entfernen des tmp-Ordners: %s", e)

    except Exception:
        logger.exception("Fehler beim Aufräumen temporärer Dateien")
