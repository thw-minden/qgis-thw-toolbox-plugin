import os
import shutil
import time
import uuid
from collections.abc import Callable

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from ..logging_utils import get_logger
from .fields import build_qgs_fields

logger = get_logger(__name__)

# Display name in the QGIS layer panel
LAYER_DISPLAY_NAME = "THW Toolbox Marker"
# Layer name inside the GeoPackage
GPKG_LAYER_NAME = "taktische_zeichen"
# Suffix appended to the project filename for the per-project GeoPackage
GPKG_SUFFIX = "_taktischezeichen"


class LayerManager:
    """Owns the THW Toolbox feature layer (GeoPackage-backed).

    Responsibilities:
    - Create or load the layer when the plugin activates, including
      schema migration for older GeoPackages that lack newer fields.
    - On project save, move the GeoPackage next to the project file so
      the layer travels with the project.

    The plugin keeps responsibility for renderer/labeling/tool wiring
    after a layer change — this manager handles layer lifecycle only.
    """

    def __init__(
        self,
        canvas,
        plugin_dir: str,
        settings,
        error_alert: Callable[[str, str, str | None], None],
        check_map_available: Callable[[], bool],
    ):
        self.canvas = canvas
        self.plugin_dir = plugin_dir
        self.settings = settings
        self._error_alert = error_alert
        self._check_map_available = check_map_available
        self.layer: QgsVectorLayer | None = None
        # True while we deliberately swap the layer (save / reprojection), so the
        # plugin does not mistake the removal for the user deleting the layer.
        self.replacing = False

    # ------------------------------------------------------------------
    # Layer creation / loading
    # ------------------------------------------------------------------

    def init_layer(self) -> QgsVectorLayer | None:
        """Create or load the layer; store and return it. None on failure."""
        logger.debug("init_layer wird aufgerufen")

        if not self._check_map_available():
            logger.debug("Keine Karte vorhanden, kann Layer nicht initialisieren")
            return None

        proj = QgsProject.instance()
        pfile = proj.fileName()
        logger.debug("Projektdatei: %s", pfile)

        existing_layers = proj.mapLayersByName(LAYER_DISPLAY_NAME)
        logger.debug("Bestehende Layer gefunden: %d", len(existing_layers))
        if existing_layers:
            self.layer = existing_layers[0]
            logger.debug("Verwende bestehenden Layer: %s", self.layer)
            return self.layer

        crs = self.canvas.mapSettings().destinationCrs().authid()
        gpkg = self._gpkg_path_for_project(proj, pfile)

        # Create or load
        if os.path.exists(gpkg):
            uri = f"{gpkg}|layername={GPKG_LAYER_NAME}"
            lyr = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")

            # Migrate older layers that lack newer fields
            existing_fields = [field.name() for field in lyr.fields()]
            if "scale_with_map" not in existing_fields or "svg_content" not in existing_fields:
                self._update_layer_fields(lyr, gpkg, GPKG_LAYER_NAME)
                lyr = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")
        else:
            lyr = self._create_new_layer(gpkg, GPKG_LAYER_NAME, crs)
            if lyr is None:
                return None

        self.layer = lyr
        logger.debug("Neuer Layer gesetzt: %s", self.layer)
        proj.addMapLayer(lyr)
        logger.debug("Layer zum Projekt hinzugefügt")
        return self.layer

    def _gpkg_path_for_project(self, proj: QgsProject, pfile: str) -> str:
        """Return the GeoPackage path: next to the project file, or in tmp/."""
        if pfile:
            return os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"

        # Project not yet saved — write into the plugin's tmp/ with a unique name
        tmp_dir = os.path.join(self.plugin_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        proj_id = proj.title() or "unnamed"
        safe_name = "".join(c for c in proj_id if c.isalnum() or c in (" ", "-", "_")).rstrip() or "project"
        safe_name = f"{safe_name}_{int(time.time())}"
        path = os.path.join(tmp_dir, f"{safe_name}{GPKG_SUFFIX}.gpkg")
        logger.debug("Erstelle eindeutige Datei für ungespeichertes Projekt: %s", path)
        return path

    def _create_new_layer(self, gpkg: str, lname: str, crs: str) -> QgsVectorLayer | None:
        """Create a new GeoPackage with the full schema and return its loaded layer."""
        try:
            os.makedirs(os.path.dirname(gpkg), exist_ok=True)

            expected_fields = build_qgs_fields()
            expected_names = [f.name() for f in expected_fields]

            mem = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
            dp = mem.dataProvider()
            if not dp.addAttributes(expected_fields):
                self._error_alert(
                    "Layer-Erstellungsfehler",
                    "Konnte Felder nicht zum Memory-Layer hinzufügen.",
                    f"Felder: {expected_names}\nVermutete Ursache: Qt6/QGIS-4 Inkompatibilität bei Feldtypen.",
                )
                return None
            mem.updateFields()

            mem_field_names = [f.name() for f in mem.fields()]
            missing_in_mem = [n for n in expected_names if n not in mem_field_names]
            if missing_in_mem:
                self._error_alert(
                    "Layer-Erstellungsfehler",
                    "Memory-Layer enthält nach addAttributes nicht alle erwarteten Felder.",
                    f"Fehlend: {missing_in_mem}\nVorhanden: {mem_field_names}",
                )
                return None

            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = lname
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                mem, gpkg, QgsProject.instance().transformContext(), opts
            )

            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                self._error_alert(
                    "Layer-Erstellungsfehler",
                    f"Konnte neuen Layer nicht erstellen: {result[1]}",
                    f"Pfad: {gpkg}\nFehler: {result[1]}",
                )
                return None

            uri = f"{gpkg}|layername={lname}"
            new_layer = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")

            new_field_names = [f.name() for f in new_layer.fields()]
            missing_in_gpkg = [n for n in expected_names if n not in new_field_names]
            if missing_in_gpkg:
                self._error_alert(
                    "Layer-Erstellungsfehler",
                    "GeoPackage wurde geschrieben, aber Felder fehlen im neuen Layer.",
                    f"Fehlend: {missing_in_gpkg}\nVorhanden: {new_field_names}\nPfad: {gpkg}",
                )
                return None

            return new_layer

        except Exception as e:
            logger.exception("Fehler beim Erstellen des neuen Layers")
            self._error_alert(
                "Layer-Erstellungsfehler", "Konnte neuen Layer nicht erstellen", f"Pfad: {gpkg}\nFehler: {e}"
            )
            return None

    def _update_layer_fields(self, old_layer: QgsVectorLayer, gpkg: str, lname: str) -> None:
        """Migrate features from `old_layer` into a freshly-schema'd GeoPackage at `gpkg`.

        Loads existing features, copies their attributes (defaulting where
        a field didn't exist before), writes to a `.temp` file, then swaps
        it over the original. The map-available check is repeated here
        because this runs at layer-load time and needs a CRS.
        """
        try:
            if not self._check_map_available():
                self._error_alert(
                    "Keine Karte vorhanden",
                    "Bitte ziehen Sie zuerst eine Karte in das Projekt ein.",
                    "Das Plugin benötigt eine Karte mit einem Koordinatensystem (CRS), um den Layer zu aktualisieren.\n\n"
                    "So fügen Sie eine Karte hinzu:\n"
                    "1. Gehen Sie zu 'Browser' im QGIS-Fenster\n"
                    "2. Ziehen Sie eine Karte (z.B. OpenStreetMap) in das Projekt\n"
                    "3. Versuchen Sie es erneut",
                )
                return

            if old_layer.isEditable():
                try:
                    old_layer.commitChanges()
                except Exception:
                    try:
                        old_layer.rollBack()
                    except Exception as e:
                        logger.debug("rollBack on old layer failed: %s", e)

            crs = self.canvas.mapSettings().destinationCrs().authid()
            mem = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
            dp = mem.dataProvider()
            dp.addAttributes(build_qgs_fields())
            mem.updateFields()

            existing_fields = [field.name() for field in old_layer.fields()]
            for feat in old_layer.getFeatures():
                new_feat = QgsFeature(mem.fields())
                new_feat.setGeometry(feat.geometry())
                new_feat.setAttribute("name", feat.attribute("name"))
                new_feat.setAttribute("svg_path", feat.attribute("svg_path"))
                new_feat.setAttribute(
                    "svg_content", feat.attribute("svg_content") if "svg_content" in existing_fields else ""
                )
                new_feat.setAttribute("size", feat.attribute("size"))
                new_feat.setAttribute(
                    "scale_with_map",
                    feat.attribute("scale_with_map") if "scale_with_map" in existing_fields else False,
                )
                new_feat.setAttribute(
                    "unique_id",
                    feat.attribute("unique_id") if "unique_id" in existing_fields else str(uuid.uuid4()),
                )

                # Default label from filename if missing
                if "label" in existing_fields and feat.attribute("label"):
                    new_feat.setAttribute("label", feat.attribute("label"))
                else:
                    svg_name = feat.attribute("name") or os.path.basename(feat.attribute("svg_path"))
                    new_feat.setAttribute("label", os.path.splitext(svg_name)[0].replace("_", " "))

                new_feat.setAttribute(
                    "show_label", feat.attribute("show_label") if "show_label" in existing_fields else False
                )
                new_feat.setAttribute(
                    "white_background",
                    feat.attribute("white_background") if "white_background" in existing_fields else False,
                )
                new_feat.setAttribute("rotation", feat.attribute("rotation") if "rotation" in existing_fields else 0.0)
                mem.dataProvider().addFeature(new_feat)

            # Remove old layer from project so QGIS releases the file lock
            QgsProject.instance().removeMapLayer(old_layer.id())
            time.sleep(0.1)

            # Write to .temp first, then atomically swap
            temp_gpkg = gpkg + ".temp"
            if os.path.exists(temp_gpkg):
                try:
                    os.remove(temp_gpkg)
                except Exception as e:
                    logger.debug("Konnte Temp-GPKG nicht löschen %s: %s", temp_gpkg, e)

            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = lname
            opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile

            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                mem, temp_gpkg, QgsProject.instance().transformContext(), opts
            )
            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                self._error_alert(
                    "Layer-Aktualisierungsfehler",
                    f"Konnte Layer-Felder nicht aktualisieren: {result[1]}",
                    f"Pfad: {gpkg}\nFehler: {result[1]}",
                )
                return

            time.sleep(0.1)
            self._swap_temp_into_place(temp_gpkg, gpkg)

        except Exception as e:
            logger.exception("Fehler beim Aktualisieren der Layer-Felder")
            self._error_alert(
                "Layer-Aktualisierungsfehler",
                "Konnte Layer-Felder nicht aktualisieren",
                f"Pfad: {gpkg}\nFehler: {e}",
            )

    def _swap_temp_into_place(self, temp_gpkg: str, gpkg: str) -> None:
        """Move temp_gpkg → gpkg, falling back to copy+delete if move fails."""
        try:
            if os.path.exists(gpkg):
                try:
                    os.remove(gpkg)
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning("Konnte ursprüngliche Datei nicht löschen: %s", e)
            shutil.move(temp_gpkg, gpkg)
        except Exception as e:
            try:
                shutil.copy2(temp_gpkg, gpkg)
                try:
                    os.remove(temp_gpkg)
                except Exception as e:
                    logger.debug("Konnte Temp-GPKG nach copy2 nicht löschen %s: %s", temp_gpkg, e)
            except Exception as e2:
                self._error_alert(
                    "Layer-Aktualisierungsfehler",
                    f"Konnte aktualisierte Datei nicht speichern: {e2}",
                    f"Pfad: {gpkg}\nFehler: {e2}",
                )
                return
            logger.warning("Temporäre Datei konnte nicht verschoben werden: %s", e)

    # ------------------------------------------------------------------
    # CRS migration
    # ------------------------------------------------------------------

    def reproject_to(
        self,
        target_crs_authid: str,
        log: Callable[[str, bool], None] | None = None,
    ) -> QgsVectorLayer | None:
        """Reproject the marker layer's geometries into ``target_crs_authid``.

        Rewrites the underlying GeoPackage with the new CRS via a temp file
        swap (same pattern as ``_update_layer_fields``). Returns the new
        layer instance on success, or the existing one if the layer is
        already in the target CRS or there is nothing to migrate. Returns
        ``None`` on failure (an error message is dispatched via ``log``).
        """
        if not self.layer:
            return None

        target_crs = QgsCoordinateReferenceSystem(target_crs_authid)
        if not target_crs.isValid():
            if log:
                log(f"Ungültiges Ziel-CRS: {target_crs_authid}", True)
            return None

        source_crs = self.layer.crs()
        if source_crs.authid() == target_crs.authid():
            return self.layer

        proj = QgsProject.instance()
        transform = QgsCoordinateTransform(source_crs, target_crs, proj)

        if self.layer.isEditable():
            try:
                self.layer.commitChanges()
            except Exception:
                try:
                    self.layer.rollBack()
                except Exception as e:
                    logger.debug("rollBack on self.layer failed: %s", e)

        gpkg = self.layer.source().split("|")[0]
        if not os.path.exists(gpkg):
            if log:
                log(f"GeoPackage nicht gefunden: {gpkg}", True)
            return None

        # Use a sibling `.gpkg` path so OGR doesn't mangle the extension when
        # writing — a `.temp` suffix can cause the writer to append `.gpkg`
        # itself and leave us pointing at a non-existent file.
        base, _ = os.path.splitext(gpkg)
        migrate_gpkg = f"{base}.__migrate__.gpkg"

        try:
            mem = QgsVectorLayer(f"Point?crs={target_crs.authid()}", "temp", "memory")
            dp = mem.dataProvider()
            dp.addAttributes(build_qgs_fields())
            mem.updateFields()

            field_names = [f.name() for f in mem.fields()]
            source_fields = {f.name() for f in self.layer.fields()}

            for feat in self.layer.getFeatures():
                new_feat = QgsFeature(mem.fields())
                geom = QgsGeometry(feat.geometry())
                if not geom.isNull():
                    if geom.transform(transform) != 0:
                        if log:
                            log("Geometrie-Transformation fehlgeschlagen.", True)
                        return None
                new_feat.setGeometry(geom)
                for name in field_names:
                    if name in source_fields:
                        new_feat.setAttribute(name, feat.attribute(name))
                dp.addFeature(new_feat)

            # Step 1: write the reprojected layer to a sibling file. Keep the
            # original layer registered (and its file untouched) until the
            # write actually produced a valid file on disk.
            if os.path.exists(migrate_gpkg):
                try:
                    os.remove(migrate_gpkg)
                except Exception as e:
                    logger.debug("Konnte Migrations-GPKG nicht löschen %s: %s", migrate_gpkg, e)

            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = GPKG_LAYER_NAME
            opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile

            result = QgsVectorFileWriter.writeAsVectorFormatV3(mem, migrate_gpkg, proj.transformContext(), opts)
            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                if log:
                    log(f"Konnte reprojizierten Layer nicht schreiben: {result[1]}", True)
                return None

            written_path = result[2] if len(result) > 2 and result[2] else migrate_gpkg
            if not os.path.exists(written_path):
                if log:
                    log(
                        f"Migrations-Datei wurde nicht erzeugt (erwartet: {written_path}).",
                        True,
                    )
                return None

            # Step 2: only NOW release the original layer's file lock and
            # swap the migrated file over it.
            self._remove_for_swap(self.layer.id())
            self.layer = None
            time.sleep(0.1)

            if not self._move_over(written_path, gpkg):
                # Swap failed but the migrated file is still on disk — keep
                # the user's data by registering it under its temp name.
                if os.path.exists(written_path):
                    uri = f"{written_path}|layername={GPKG_LAYER_NAME}"
                    fallback = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")
                    if fallback.isValid():
                        QgsProject.instance().addMapLayer(fallback)
                        self.layer = fallback
                        if log:
                            log(
                                "Migration unter Behelfsnamen gespeichert: "
                                f"{written_path}. Bitte Projekt speichern oder "
                                "Datei manuell umbenennen.",
                                True,
                            )
                        return fallback
                if log:
                    log("Datei-Tausch nach Migration fehlgeschlagen.", True)
                return None

            uri = f"{gpkg}|layername={GPKG_LAYER_NAME}"
            new_layer = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")
            if not new_layer.isValid():
                if log:
                    log("Reprojizierter Layer konnte nicht geladen werden.", True)
                return None
            QgsProject.instance().addMapLayer(new_layer)
            self.layer = new_layer
            return new_layer

        except Exception as e:
            logger.exception("Fehler beim Reprojizieren des Layers")
            if log:
                log(f"Reprojektion fehlgeschlagen: {e}", True)
            # If we removed the original layer before the failure, try to
            # restore it from whichever file is still on disk.
            if self.layer is None:
                for candidate in (gpkg, migrate_gpkg):
                    if os.path.exists(candidate):
                        uri = f"{candidate}|layername={GPKG_LAYER_NAME}"
                        restored = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")
                        if restored.isValid():
                            QgsProject.instance().addMapLayer(restored)
                            self.layer = restored
                            break
            return None

    def _move_over(self, src: str, dst: str) -> bool:
        """Best-effort move of ``src`` over ``dst``. Returns True on success."""
        try:
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning("Konnte Ziel-Datei nicht löschen: %s", e)
            shutil.move(src, dst)
            return True
        except Exception as e:
            logger.warning("shutil.move %s -> %s fehlgeschlagen: %s", src, dst, e)
            try:
                shutil.copy2(src, dst)
                try:
                    os.remove(src)
                except Exception as e:
                    logger.debug("Konnte Quell-Datei nach copy2 nicht löschen %s: %s", src, e)
                return True
            except Exception as e2:
                logger.warning("shutil.copy2 %s -> %s fehlgeschlagen: %s", src, dst, e2)
                return False

    # ------------------------------------------------------------------
    # Project-save handling
    # ------------------------------------------------------------------

    def _remove_for_swap(self, layer_id: str) -> None:
        """Remove the marker layer ahead of re-adding a replacement."""
        self.replacing = True
        try:
            QgsProject.instance().removeMapLayer(layer_id)
        finally:
            self.replacing = False

    def on_project_save(self) -> QgsVectorLayer | None:
        """Move the GeoPackage next to the project file. Returns the new layer (or None)."""
        logger.debug("Projekt wird gespeichert, verschiebe Layer-Datei zum Projektpfad")
        if not self.layer:
            return None

        if self.layer.providerType() != "ogr":
            logger.debug("Layer ist keine GeoPackage, nichts zu tun")
            return None

        proj = QgsProject.instance()
        pfile = proj.fileName()
        if not pfile:
            logger.debug("Kein Projektpfad verfügbar")
            return None

        current_source = self.layer.source().split("|")[0]
        if not os.path.exists(current_source):
            logger.debug("Aktuelle Layer-Datei existiert nicht: %s", current_source)
            return None

        new_gpkg = os.path.splitext(pfile)[0] + GPKG_SUFFIX + ".gpkg"
        if os.path.abspath(current_source) == os.path.abspath(new_gpkg):
            logger.debug("Datei ist bereits am richtigen Ort")
            return None

        try:
            target_dir = os.path.dirname(new_gpkg)
            os.makedirs(target_dir, exist_ok=True)
            if not os.access(target_dir, os.W_OK):
                raise Exception(f"Keine Schreibrechte im Zielverzeichnis: {target_dir}")

            try:
                if self.layer.isEditable():
                    logger.debug("Layer ist im Bearbeitungsmodus - committe Änderungen vor Export")
                    self.layer.commitChanges()
            except Exception as e:
                logger.debug("Hinweis beim Committen vor Export: %s", e)

            if os.path.exists(new_gpkg):
                try:
                    os.remove(new_gpkg)
                except Exception as e:
                    logger.warning("Konnte Ziel-Datei nicht löschen: %s", e)

            logger.debug("Kopiere Layer-Daten von %s nach %s", current_source, new_gpkg)
            save_options = QgsVectorFileWriter.SaveVectorOptions()
            save_options.driverName = "GPKG"
            save_options.layerName = GPKG_LAYER_NAME
            save_options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile

            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                self.layer, new_gpkg, proj.transformContext(), save_options
            )

            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                logger.warning("Writer-Export fehlgeschlagen: %s - versuche Datei-Kopie als Fallback", result[1])
                shutil.copy2(current_source, new_gpkg)

            logger.debug("Layer-Daten erfolgreich exportiert/kopiert")

            # Swap project's layer reference
            self._remove_for_swap(self.layer.id())
            uri = f"{new_gpkg}|layername={GPKG_LAYER_NAME}"
            new_layer = QgsVectorLayer(uri, LAYER_DISPLAY_NAME, "ogr")
            if not new_layer.isValid():
                raise Exception(f"Neuer Layer ist nicht gültig: {new_layer.error().message()}")

            QgsProject.instance().addMapLayer(new_layer)
            self.layer = new_layer

            # Try to remove the old file (best effort — may still be locked)
            try:
                time.sleep(0.5)
                os.remove(current_source)
                logger.debug("Alte Datei gelöscht: %s", current_source)
            except Exception as e:
                logger.warning("Konnte alte Datei nicht löschen (wird beim nächsten Start bereinigt): %s", e)

            logger.debug("Layer erfolgreich zum Projektpfad verschoben")
            return new_layer

        except Exception as e:
            logger.exception("Fehler beim Verschieben der Layer-Datei")
            self._error_alert(
                "Fehler beim Projekt-Speichern",
                "Konnte Layer-Datei nicht zum Projektpfad verschieben",
                f"Von: {current_source}\nNach: {new_gpkg}\nFehler: {e}\n\n"
                "Hinweis: Die Layer-Daten bleiben im ursprünglichen Verzeichnis erhalten.",
            )
            return None
