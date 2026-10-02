import glob
import hashlib
import os
import shutil
import time

from ..logging_utils import get_logger

logger = get_logger(__name__)

# Alte GeoPackage-Snapshots im tmp/ älter als 24h löschen
_GPKG_AGE_THRESHOLD_S = 24 * 60 * 60
# SVG-/Preview-Cache-Dateien älter als 1h löschen
_CACHE_AGE_THRESHOLD_S = 60 * 60
# Wie viele SVG-Cache-Dateien wir maximal behalten (LRU-artig per ctime)
_SVG_CACHE_KEEP = 50


def cleanup_temp_files(plugin_dir: str) -> None:
    """Räume Plugin-Temp-Verzeichnisse auf (idempotent, fail-safe pro Pfad).

    Wird beim Plugin-Activate aufgerufen, damit alte Snapshots und
    SVG-Cache-Dateien nicht ewig im Plugin-Verzeichnis liegen bleiben.
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

        # 2) SVG-/Preview-Cache-Dateien
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


def create_temp_svg_from_content(plugin_dir: str, svg_content: str, feature_id) -> str | None:
    """Materialisiere einen SVG-Blob als Datei im svg_cache und gib den Pfad zurück.

    Wird vom Renderer benutzt, wenn ein Feature seinen SVG-Inhalt inline
    gespeichert hat (z.B. nach Portable-Import ohne Original-svgs/-Ordner).
    QgsSvgMarkerSymbolLayer braucht einen Datei-Pfad, also schreiben wir
    den Inhalt in einen Cache und geben den Pfad zurück.

    Behält maximal 50 Dateien (älteste per ctime werden gelöscht).
    Bei Fehler: gibt None zurück; Caller-Code hat einen Fallback auf den
    gespeicherten svg_path.
    """
    cache_dir = os.path.join(plugin_dir, "temp_files", "svg_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Inhalts-Hash im Namen: QGIS cached SVGs pro Pfad — nach einem Zeichen-Wechsel
    # würde sonst noch eine Weile das alte Bild gezeichnet.
    digest = hashlib.md5(svg_content.encode("utf-8")).hexdigest()[:10]
    temp_path = os.path.join(cache_dir, f"feature_{feature_id}_{digest}.svg")

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(svg_content)

        # LRU-Cleanup
        cached = [name for name in os.listdir(cache_dir) if name.startswith("feature_")]
        if len(cached) > _SVG_CACHE_KEEP:
            cached.sort(key=lambda x: os.path.getctime(os.path.join(cache_dir, x)))
            for old in cached[:-_SVG_CACHE_KEEP]:
                try:
                    os.remove(os.path.join(cache_dir, old))
                except Exception as e:
                    logger.debug("Konnte alte Cache-Datei %s nicht löschen: %s", old, e)

        return temp_path
    except Exception as e:
        logger.warning("Fehler beim Erstellen der temporären SVG-Datei (feature %s): %s", feature_id, e)
        return None
