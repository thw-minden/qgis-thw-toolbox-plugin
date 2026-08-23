import os

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from .export.dji_kml_export import DjiKmlExporter
from .export.dji_mbtiles_export import DjiMbtilesExporter
from .export.dji_mbtiles_export import _ZoomDialog as _MbtilesZoomDialog
from .export.portable_export import PortableExporter
from .layer.feature_ops import FeatureOperations
from .layer.labeling import apply_labeling
from .layer.layer_manager import LayerManager
from .layer.renderer import apply_renderer
from .layout.mgrs_grid import build_mgrs_grid_layer
from .logging_utils import get_logger
from .paths import plugin_root
from .settings import THWToolboxSettings
from .tools import style_library
from .tools.canvas_drop_filter import CanvasDropFilter
from .tools.identify_tool import IdentifyTool
from .tools.move_tool import MoveTool
from .ui.config_dialog import ConfigDialog
from .ui.nominatim_search_dialog import NominatimSearchDialog
from .ui.setup_dialog import SetupDialog
from .ui.svg_dock import SvgDock
from .ui.template_dialog import TemplateDialog
from .util.temp_files import cleanup_temp_files

logger = get_logger(__name__)


class THWToolboxPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = plugin_root()
        self.layer = None
        self.layer_manager = None
        self.feature_ops = None
        self.current_svg = None
        self.drop_filter = None
        self.ident_tool = None
        self.move_tool = None
        self.action = None
        self.dock = None
        # True während QGIS das Projekt leert (Projekt schließen / anderes laden).
        # In dem Fall sollen wir die Layer-Entfernung still hinnehmen statt den
        # Nutzer mit der Deaktivierungs-Warnung zu konfrontieren.
        self._project_clearing = False

        self.settings = THWToolboxSettings()

    def _check_map_available(self):
        """Prüft, ob eine Karte vorhanden ist (CRS gesetzt und Layer im Projekt)."""
        try:
            # Prüfe, ob ein gültiges CRS gesetzt ist
            crs = self.canvas.mapSettings().destinationCrs()
            if not crs.isValid():
                return False

            # Prüfe, ob es Layer im Projekt gibt (außer dem THW Toolbox Marker Layer)
            project_layers = QgsProject.instance().mapLayers().values()
            # Zähle Layer, die nicht unser eigener Marker-Layer sind
            other_layers = [lyr for lyr in project_layers if lyr.name() != "THW Toolbox Marker"]

            # Wenn keine anderen Layer vorhanden sind, ist wahrscheinlich keine Karte geladen
            if len(other_layers) == 0:
                return False

            return True
        except Exception as e:
            logger.warning("Fehler bei _check_map_available: %s", e)
            return False

    def _show_error_alert(self, title, message, details=None):
        """Zeigt einen Fehler-Alert mit optionalen Details."""
        msg_box = QMessageBox(self.iface.mainWindow())
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)

        if details:
            msg_box.setDetailedText(details)

        msg_box.exec()

        # Zusätzlich auch in der Message Bar anzeigen
        try:  # QGIS4 Variant
            self.iface.messageBar().pushMessage(
                title,
                message,
                level=Qgis.MessageLevel.Critical,  # Critical level
            )
        except Exception:
            # QGIS3 Variant
            self.iface.messageBar().pushMessage(
                title,
                message,
                level=3,  # Critical level
            )

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "icon.svg"))
        self.action = QAction(icon, "THW Toolbox", self.iface.mainWindow())
        self.action.setCheckable(True)  # Macht das Symbol zu einem Toggle-Button
        self.action.triggered.connect(self.toggle_plugin)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("THW Toolbox", self.action)

        # Setup-Aktion (Projekt-Setup + Basiskarten)
        setup_icon = QIcon(os.path.join(self.plugin_dir, "icons", "setup.svg"))
        self.setup_action = QAction(setup_icon, "Toolbox Setup", self.iface.mainWindow())
        self.setup_action.triggered.connect(self._open_setup_dialog)
        self.iface.addToolBarIcon(self.setup_action)
        self.iface.addPluginToMenu("THW Toolbox", self.setup_action)

        # Settings-Aktion in Toolbar neben dem Toolbox-Icon
        settings_icon = QIcon(os.path.join(self.plugin_dir, "icons", "settings.svg"))
        self.settings_action = QAction(settings_icon, "Toolbox Einstellungen", self.iface.mainWindow())
        self.settings_action.triggered.connect(self._open_config_dialog)
        self.iface.addToolBarIcon(self.settings_action)
        self.iface.addPluginToMenu("THW Toolbox", self.settings_action)

        # Druckvorlagen
        template_icon = QIcon(os.path.join(self.plugin_dir, "icons", "template.svg"))
        self.template_action = QAction(template_icon, "Druckvorlagen", self.iface.mainWindow())
        self.template_action.triggered.connect(self._open_template_dialog)
        self.iface.addToolBarIcon(self.template_action)
        self.iface.addPluginToMenu("THW Toolbox", self.template_action)

        # MGRS-Gitter als temporären Layer hinzufügen
        mgrs_icon = QIcon(os.path.join(self.plugin_dir, "icons", "mgrs.svg"))
        self.mgrs_grid_action = QAction(mgrs_icon, "MGRS-Gitter temporär hinzufügen", self.iface.mainWindow())
        self.mgrs_grid_action.triggered.connect(self._add_mgrs_grid_layer)
        self.iface.addToolBarIcon(self.mgrs_grid_action)
        self.iface.addPluginToMenu("THW Toolbox", self.mgrs_grid_action)

        # Adress-Suche (Nominatim)
        search_icon = QIcon(os.path.join(self.plugin_dir, "icons", "search.svg"))
        self.search_action = QAction(search_icon, "Adresse suchen", self.iface.mainWindow())
        self.search_action.setShortcut(QKeySequence("Alt+S"))
        self.search_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.search_action.triggered.connect(self._open_search_dialog)
        self.iface.addToolBarIcon(self.search_action)
        self.iface.addPluginToMenu("THW Toolbox", self.search_action)

        # Export-Aktion hinzufügen
        self.export_action = QAction("Portables Paket exportieren", self.iface.mainWindow())
        self.export_action.triggered.connect(self._export_portable_package)
        self.iface.addPluginToMenu("THW Toolbox", self.export_action)

        # Drohnen-Export: Flugrouten (KMZ/KML) und Ebenenlayer (MBTiles)
        self.dji_export_action = QAction("Flugrouten für Drohne exportieren (KMZ)", self.iface.mainWindow())
        self.dji_export_action.triggered.connect(self._export_selected_layer_as_dji)
        self.iface.addPluginToMenu("THW Toolbox", self.dji_export_action)

        self.dji_mbtiles_action = QAction("Ebenenlayer für Drohne exportieren (MBTiles)", self.iface.mainWindow())
        self.dji_mbtiles_action.triggered.connect(self._export_selected_layer_as_mbtiles)
        self.iface.addPluginToMenu("THW Toolbox", self.dji_mbtiles_action)

        # Verbinde Projekt-Events für automatisches Speichern
        QgsProject.instance().writeProject.connect(self._on_project_save)
        # Reagiere auf Layer-Entfernung, damit wir das Plugin sauber deaktivieren,
        # wenn der Marker-Layer aus dem Projekt gelöscht wird.
        QgsProject.instance().layersWillBeRemoved.connect(self._on_layers_will_be_removed)
        # Projekt-Clear (Schließen / anderes Projekt laden) erkennen, damit die
        # Layer-Entfernung in diesem Fall keinen Warnhinweis auslöst.
        try:
            QgsProject.instance().aboutToBeCleared.connect(self._on_project_about_to_be_cleared)
        except AttributeError:
            pass
        QgsProject.instance().cleared.connect(self._on_project_cleared)

        # Default-Style-Cache nachfüllen (QGIS-4 lädt nicht zuverlässig alle
        # DB-Einträge in mSymbols beim Start). Deferred via QTimer, damit der
        # Plugin-Init nicht blockiert.
        QTimer.singleShot(0, self._rehydrate_style_cache)

    def _rehydrate_style_cache(self):
        try:
            count = style_library.rehydrate_cache(self.plugin_dir)
            if count:
                logger.info("THW Toolbox: %d Stile in den Cache nachgeladen", count)
        except Exception:
            logger.exception("Stil-Rehydrate fehlgeschlagen")

    def unload(self):
        # Plugin deaktivieren falls aktiv
        if self.action and self.action.isChecked():
            self.deactivate()

        if self.dock:
            self.iface.removeDockWidget(self.dock)
        if self.drop_filter:
            self.canvas.viewport().removeEventFilter(self.drop_filter)
        if self.ident_tool:
            self.canvas.unsetMapTool(self.ident_tool)
        if self.move_tool:
            self.canvas.unsetMapTool(self.move_tool)
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("THW Toolbox", self.action)
        if self.setup_action:
            self.iface.removeToolBarIcon(self.setup_action)
            self.iface.removePluginMenu("THW Toolbox", self.setup_action)
        if self.settings_action:
            self.iface.removeToolBarIcon(self.settings_action)
            self.iface.removePluginMenu("THW Toolbox", self.settings_action)
        if self.search_action:
            self.iface.removeToolBarIcon(self.search_action)
            self.iface.removePluginMenu("THW Toolbox", self.search_action)
        if self.template_action:
            self.iface.removeToolBarIcon(self.template_action)
            self.iface.removePluginMenu("THW Toolbox", self.template_action)
        if getattr(self, "mgrs_grid_action", None):
            self.iface.removeToolBarIcon(self.mgrs_grid_action)
            self.iface.removePluginMenu("THW Toolbox", self.mgrs_grid_action)
            self.mgrs_grid_action = None
        if self.export_action:
            self.iface.removePluginMenu("THW Toolbox", self.export_action)

        if getattr(self, "dji_export_action", None):
            self.iface.removePluginMenu("THW Toolbox", self.dji_export_action)
            self.dji_export_action = None
        if getattr(self, "dji_mbtiles_action", None):
            self.iface.removePluginMenu("THW Toolbox", self.dji_mbtiles_action)
            self.dji_mbtiles_action = None

        # Trenne Projekt-Events
        QgsProject.instance().writeProject.disconnect(self._on_project_save)
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_layers_will_be_removed)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().aboutToBeCleared.disconnect(self._on_project_about_to_be_cleared)
        except (AttributeError, TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except (TypeError, RuntimeError):
            pass

        # Räume temporäre Dateien auf
        cleanup_temp_files(self.plugin_dir)

    def activate(self):
        logger.debug("Plugin wird aktiviert")

        # Bereinige alte temporäre Dateien beim Start
        cleanup_temp_files(self.plugin_dir)

        if self.layer_manager is None:
            self.layer_manager = LayerManager(
                self.canvas,
                self.plugin_dir,
                self.settings,
                self._show_error_alert,
                self._check_map_available,
            )
        self.layer = self.layer_manager.init_layer()
        if self.feature_ops is None:
            self.feature_ops = FeatureOperations(
                layer_provider=lambda: self.layer,
                settings=self.settings,
                plugin_dir=self.plugin_dir,
                canvas=self.canvas,
                error_alert=self._show_error_alert,
                on_renderer_dirty=self._update_renderer,
                on_labeling_dirty=self._on_labeling_dirty,
            )
        if self.layer:
            self._init_renderer(self.layer)
        logger.debug("Layer initialisiert: %s", self.layer)

        # Prüfe erneut, ob der Layer erfolgreich initialisiert wurde
        if not self.layer:
            # Layer konnte nicht initialisiert werden (z.B. wegen fehlender Karte)
            if self.action:
                self.action.setChecked(False)
            return

        self._init_dock()
        # Drag & Drop
        if not self.drop_filter:
            logger.debug("Erstelle CanvasDropFilter")
            df = CanvasDropFilter(self.canvas, self._place_feature)
            self.drop_filter = df
            self.canvas.viewport().installEventFilter(df)
            self.canvas.setAcceptDrops(True)
            logger.debug("CanvasDropFilter installiert")
        # IdentifyTool
        if not self.ident_tool:
            self.ident_tool = IdentifyTool(self.canvas, self)
        else:
            # IdentifyTool existiert bereits, zeige das Feature-Dock an
            if hasattr(self.ident_tool, "feature_dock"):
                self.ident_tool.feature_dock.show()
                self.ident_tool.feature_dock.raise_()
        # MoveTool
        if not self.move_tool:
            self.move_tool = MoveTool(self.canvas, self)
        self.canvas.setMapTool(self.move_tool)

        # Load the configuration settings
        self.settings.load_settings(QgsProject.instance())

        # Plugin-Symbol als aktiv markieren
        if self.action:
            self.action.setChecked(True)

    def toggle_plugin(self):
        """Schaltet das Plugin ein/aus basierend auf dem aktuellen Zustand des Symbols."""
        if self.action.isChecked():
            self.activate()
        else:
            self.deactivate()

    def deactivate(self):
        """Deaktiviert das Plugin und setzt das Symbol zurück."""
        logger.debug("Plugin wird deaktiviert")

        # Canvas-Tool zurücksetzen
        if self.move_tool:
            try:
                if self.canvas.mapTool() == self.move_tool:
                    self.canvas.unsetMapTool(self.move_tool)
            except RuntimeError:
                pass

        # Dock verstecken
        if self.dock:
            self.dock.hide()

        # Feature-Dock verstecken
        if self.ident_tool and hasattr(self.ident_tool, "feature_dock"):
            self.ident_tool.feature_dock.hide()

        # Plugin-Symbol als inaktiv markieren
        if self.action:
            self.action.setChecked(False)

    def _on_project_about_to_be_cleared(self):
        self._project_clearing = True

    def _on_project_cleared(self):
        self._project_clearing = False

    def _on_layers_will_be_removed(self, layer_ids):
        """Wenn der Marker-Layer aus dem Projekt entfernt wird, Plugin sauber deaktivieren."""
        if not self.layer:
            return
        try:
            our_id = self.layer.id()
        except RuntimeError:
            # Layer-C++-Objekt ist bereits weg — trotzdem aufräumen
            our_id = None

        if our_id is None or our_id in layer_ids:
            logger.debug("Marker-Layer wird entfernt, deaktiviere Plugin")
            # Referenzen löschen, bevor die Tools weiter darauf zugreifen
            self.layer = None
            if self.layer_manager:
                self.layer_manager.layer = None
            if self.ident_tool:
                self.ident_tool.layer = None
            if self.move_tool:
                self.move_tool.layer = None

            was_active = self.action is not None and self.action.isChecked()
            if was_active:
                self.deactivate()
                # Beim Projekt-Schließen/-Wechsel werden alle Layer entfernt —
                # das ist erwartet, also keinen Warndialog zeigen.
                if not self._project_clearing:
                    self._show_error_alert(
                        "THW Toolbox deaktiviert",
                        "Der Marker-Layer „THW Toolbox Marker“ wurde aus dem Projekt entfernt.",
                        "Das Plugin benötigt diesen Layer, um Symbole zu verwalten, und wurde deshalb deaktiviert.\n\n"
                        "So reaktivieren Sie die Toolbox:\n"
                        "1. Klicken Sie erneut auf das THW-Toolbox-Symbol in der Werkzeugleiste.\n"
                        "2. Der Marker-Layer wird automatisch neu angelegt oder geladen.",
                    )

    def _init_dock(self):
        if self.dock:
            # Dock existiert bereits, zeige es an
            self.dock.show()
            self.dock.raise_()
            return

        progress = QProgressDialog(
            "Taktische Zeichen werden geladen …",
            "",
            0,
            100,
            self.iface.mainWindow(),
        )
        progress.setWindowTitle("THW Toolbox")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        progress.setValue(0)
        progress.show()
        QCoreApplication.processEvents()

        def on_load_progress(current: int, total: int, text: str):
            pct = int(current * 100 / total) if total > 0 else 100
            progress.setValue(pct)
            if text:
                progress.setLabelText(f"Taktische Zeichen werden geladen … {text}")
            QCoreApplication.processEvents()

        try:
            self.dock = QDockWidget("THW Toolbox", self.iface.mainWindow())
            self.dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
            self.svg_dock_widget = SvgDock(
                self.plugin_dir,
                self._on_svg_drag_start,
                self._open_config_dialog,
                layer_provider=lambda: self.layer,
                navigate_callback=self._navigate_to_feature,
                progress_callback=on_load_progress,
            )
            self.dock.setWidget(self.svg_dock_widget)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        finally:
            progress.close()

    def _navigate_to_feature(self, fid):
        """Zentriert die Karte auf das Feature — ohne Auswahl oder Drag-Modus."""
        if not self.layer:
            return
        feat = self.layer.getFeature(fid)
        if not feat or not feat.isValid() or not feat.geometry():
            return
        point = feat.geometry().asPoint()
        layer_crs = self.layer.crs()
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs:
            transform = QgsCoordinateTransform(layer_crs, canvas_crs, QgsProject.instance())
            try:
                point = transform.transform(point)
            except Exception:
                logger.exception("Konnte Feature-Koordinaten nicht transformieren")
                return
        self.canvas.setCenter(point)
        self.canvas.refresh()
        if self.move_tool:
            self.move_tool.moving_feature = None
            self.move_tool.set_move_mode(False)
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_svg_drag_start(self, svg_path):
        self.current_svg = svg_path

    def _init_renderer(self, layer):
        """Wendet Renderer + Labeling auf den Layer an."""
        if not layer:
            return
        apply_renderer(layer, self.plugin_dir)
        apply_labeling(layer, self.settings)

    def _setup_labeling(self, layer):
        """Wendet Labeling auf den Layer an."""
        apply_labeling(layer, self.settings)

    def _update_renderer(self):
        """Aktualisiert den Renderer mit allen Features."""
        logger.debug("_update_renderer aufgerufen")
        if not self.layer:
            logger.debug("self.layer ist None, beende _update_renderer")
            return

        logger.debug("Rufe _init_renderer auf")
        self._init_renderer(self.layer)

        # Labeling auch aktualisieren
        self._setup_labeling(self.layer)

    def _on_project_save(self):
        """Speichert Settings und delegiert das Verschieben des Layers an den LayerManager."""
        if not self.layer or not self.layer_manager:
            return
        self.settings.save_settings(QgsProject.instance())
        new_layer = self.layer_manager.on_project_save()
        if new_layer:
            self.on_layer_replaced(new_layer)

    def on_layer_replaced(self, new_layer):
        """Adopt a freshly-swapped marker layer (post-save, post-reprojection)."""
        if not new_layer or new_layer is self.layer:
            return
        self.layer = new_layer
        self._update_tool_references()
        self._init_renderer(new_layer)

    def _update_tool_references(self):
        """Aktualisiert alle Tool-Referenzen auf den aktuellen Layer."""
        if hasattr(self, "ident_tool") and self.ident_tool:
            self.ident_tool.layer = self.layer
        if hasattr(self, "move_tool") and self.move_tool:
            self.move_tool.layer = self.layer

    def _place_feature(self, svg_path, point):
        """SVG-Drop-Callback: Feature platzieren und im Dock auswählen."""
        if not self._check_map_available():
            self._show_error_alert(
                "Keine Karte vorhanden",
                "Bitte ziehen Sie zuerst eine Karte in das Projekt ein.",
                "Das Plugin benötigt eine Karte mit einem Koordinatensystem (CRS), um Symbole platzieren zu können.\n\n"
                "So fügen Sie eine Karte hinzu:\n"
                "1. Gehen Sie zu 'Browser' im QGIS-Fenster\n"
                "2. Ziehen Sie eine Karte (z.B. OpenStreetMap) in das Projekt\n"
                "3. Versuchen Sie erneut, ein Symbol zu platzieren",
            )
            return
        if not self.feature_ops:
            return

        new_feature = self.feature_ops.place_feature(svg_path, point)
        if not new_feature:
            return

        if hasattr(self, "svg_dock_widget"):
            self.svg_dock_widget.refresh_marker_list()

        # Feature im Dock auswählen + Move-Modus aktivieren
        if self.ident_tool and hasattr(self.ident_tool, "feature_dock"):
            self.ident_tool.feature_dock.show_feature(new_feature, self)
        if self.move_tool:
            self.move_tool.set_move_mode(True)

    # ------------------------------------------------------------------
    # Public callbacks (called from FeatureDock with legacy method names)
    # ------------------------------------------------------------------

    def delete_feature(self, fid):
        if not self.feature_ops or not self.feature_ops.delete(fid):
            return
        # Dock-Tree und Layer-Panel nach Delete refreshen
        if hasattr(self, "svg_dock_widget"):
            self.svg_dock_widget.treeWidget.clear()
            self.svg_dock_widget.populate_root_folders()
            self.svg_dock_widget.refresh_marker_list()
        if hasattr(self.iface, "layerTreeView") and self.layer:
            self.iface.layerTreeView().refreshLayerSymbology(self.layer.id())
        self.canvas.refresh()
        self.canvas.update()

    def resize_feature(self, fid, size):
        self.feature_ops and self.feature_ops.resize(fid, size)

    def toggle_scale(self, fid, value):
        self.feature_ops and self.feature_ops.toggle_scale(fid, value)

    def toggle_white_background(self, fid, value):
        self.feature_ops and self.feature_ops.toggle_white_background(fid, value)

    def rotate_feature(self, fid, degrees):
        self.feature_ops and self.feature_ops.rotate(fid, degrees)

    def update_feature_label(self, fid, text):
        self.feature_ops and self.feature_ops.set_label(fid, text)

    def toggle_label_visibility(self, fid, value):
        self.feature_ops and self.feature_ops.toggle_label(fid, value)

    def update_origin(self, fid, origin_x, origin_y):
        self.feature_ops and self.feature_ops.update_origin(fid, origin_x, origin_y)

    def _on_labeling_dirty(self):
        """FeatureOperations callback after a label-affecting attribute change."""
        if not self.layer:
            return
        apply_labeling(self.layer, self.settings)
        self.layer.triggerRepaint()
        if hasattr(self.iface, "layerTreeView"):
            self.iface.layerTreeView().refreshLayerSymbology(self.layer.id())
        self.canvas.refresh()

    def _open_search_dialog(self):
        NominatimSearchDialog(self.canvas, self.iface.mainWindow()).exec()

    def _open_template_dialog(self):
        TemplateDialog(self.plugin_dir, self.iface.mainWindow()).exec()

    def _add_mgrs_grid_layer(self):
        extent = self.canvas.extent()
        crs = self.canvas.mapSettings().destinationCrs()
        if not crs.isValid() or extent.isEmpty():
            self._show_error_alert(
                "MGRS-Gitter",
                "Keine gültige Kartenansicht.",
                "Öffnen Sie zuerst eine Karte und zoomen Sie auf den gewünschten Bereich.",
            )
            return
        layer, message = build_mgrs_grid_layer(extent, crs, interval=1000)
        if layer is None:
            self._show_error_alert("MGRS-Gitter", message, None)
            return
        QgsProject.instance().addMapLayer(layer)
        self.iface.messageBar().pushMessage("MGRS-Gitter", message, Qgis.MessageLevel.Success)

    def _open_setup_dialog(self):
        SetupDialog(self, self.iface.mainWindow())

    def _open_config_dialog(self):
        if ConfigDialog(self.settings, self.iface.mainWindow()).exec_and_apply():
            self.settings.save_settings(QgsProject.instance())
            if self.layer:
                self._init_renderer(self.layer)

    def _pick_vector_layers(self, title: str) -> list[QgsVectorLayer] | None:
        """Open a checkbox-list dialog for selecting one or more vector layers.

        Defaults to the active layer being pre-checked, if any.
        Returns None on cancel, or the list of chosen layers (possibly empty
        means cancel — we treat empty selection as cancel).
        """
        project = QgsProject.instance()
        vector_layers = [
            lyr for lyr in project.mapLayers().values() if isinstance(lyr, QgsVectorLayer) and lyr.isValid()
        ]
        if not vector_layers:
            self._show_error_alert(
                title,
                "Keine Vektorlayer im Projekt",
                "Fügen Sie zuerst einen Vektorlayer hinzu und rufen Sie den Menüpunkt erneut auf.",
            )
            return None

        active = self.iface.activeLayer()
        active_id = active.id() if isinstance(active, QgsVectorLayer) else None
        return _LayerPickDialog.pick(self.iface.mainWindow(), title, vector_layers, active_id)

    def _export_selected_layer_as_dji(self):
        layers = self._pick_vector_layers("Flugrouten-Export (Drohne)")
        if not layers:
            return

        if len(layers) == 1:
            target_paths = [self._ask_single_save_path(layers[0], "kmz", "KMZ für Drohne (*.kmz);;KML (*.kml)")]
            if not target_paths[0]:
                return
        else:
            folder = self._ask_output_folder("Ordner für KMZ-Dateien wählen")
            if not folder:
                return
            target_paths = [os.path.join(folder, _safe_filename(lyr.name()) + ".kmz") for lyr in layers]

        self._run_batch_export(
            title="Flugrouten-Export (Drohne)",
            layers=layers,
            target_paths=target_paths,
            run=lambda lyr, path, _on_fb, _chk: DjiKmlExporter(
                on_success=lambda _p: None,
                on_error=lambda *a: None,
            ).export(lyr, path),
        )

    def _export_selected_layer_as_mbtiles(self):
        layers = self._pick_vector_layers("Ebenenlayer-Export (Drohne)")
        if not layers:
            return

        zoom = _MbtilesZoomDialog.ask(self.iface.mainWindow())
        if zoom is None:
            return
        zoom_min, zoom_max = zoom

        if len(layers) == 1:
            target_paths = [self._ask_single_save_path(layers[0], "mbtiles", "MBTiles (*.mbtiles)")]
            if not target_paths[0]:
                return
        else:
            folder = self._ask_output_folder("Ordner für MBTiles wählen")
            if not folder:
                return
            target_paths = [os.path.join(folder, _safe_filename(lyr.name()) + ".mbtiles") for lyr in layers]

        self._run_batch_export(
            title="Ebenenlayer-Export (Drohne)",
            layers=layers,
            target_paths=target_paths,
            run=lambda lyr, path, on_fb, chk: DjiMbtilesExporter(
                on_success=lambda _p: None,
                on_error=lambda *a: None,
                on_feedback=on_fb,
                on_cancel_check=chk,
            ).export(lyr, path, zoom_min, zoom_max),
        )

    def _ask_single_save_path(self, layer: QgsVectorLayer, ext: str, file_filter: str) -> str | None:
        initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")
        initial_path = os.path.join(initial_dir, _safe_filename(layer.name()) + "." + ext)
        path, _ = QFileDialog.getSaveFileName(self.iface.mainWindow(), "Datei speichern", initial_path, file_filter)
        if not path:
            return None
        lower = path.lower()
        if ext == "kmz" and not (lower.endswith(".kmz") or lower.endswith(".kml")):
            path += ".kmz"
        elif ext == "mbtiles" and not lower.endswith(".mbtiles"):
            path += ".mbtiles"
        return path

    def _ask_output_folder(self, title: str) -> str | None:
        initial = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(initial):
            initial = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self.iface.mainWindow(), title, initial)
        return folder or None

    def _run_batch_export(self, title, layers, target_paths, run):
        """Loop over layers with a progress dialog; collect failures, show summary at end.

        The progress bar has `total * 100` steps so within-layer feedback
        (e.g. per zoom level from the MBTiles algorithm) can move it
        smoothly. Callers pass a `run(layer, path, on_feedback, cancel_check)`
        where `on_feedback(percent, text)` is optional and fires from inside
        the algorithm's feedback loop.
        """
        total = len(layers)
        progress = QProgressDialog("Export wird vorbereitet …", "Abbrechen", 0, total * 100, self.iface.mainWindow())
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QCoreApplication.processEvents()

        successes: list[str] = []
        failures: list[tuple[str, str]] = []

        for i, (lyr, path) in enumerate(zip(layers, target_paths)):
            if progress.wasCanceled():
                break
            base_label = f"„{lyr.name()}“ ({i + 1}/{total})"
            progress.setLabelText(f"Exportiere {base_label} …")
            progress.setValue(i * 100)
            QCoreApplication.processEvents()

            def on_feedback(pct: int, text: str, _i=i, _label=base_label):
                # Clamp and combine layer-step + sub-step for a continuous bar.
                pct = max(0, min(100, pct))
                progress.setValue(_i * 100 + pct)
                if text:
                    progress.setLabelText(f"Exportiere {_label} — {text}")
                QCoreApplication.processEvents()

            def check_cancel():
                QCoreApplication.processEvents()
                return progress.wasCanceled()

            try:
                ok = run(lyr, path, on_feedback, check_cancel)
                if ok:
                    successes.append(path)
                else:
                    failures.append((lyr.name(), "Export meldete Fehler (siehe Log)"))
            except Exception as e:
                logger.exception("Export fehlgeschlagen für Layer %s", lyr.name())
                failures.append((lyr.name(), str(e)))
            progress.setValue((i + 1) * 100)
            QCoreApplication.processEvents()

        progress.close()

        if successes and not failures:
            self.iface.messageBar().pushMessage(
                "Fertig",
                f"{len(successes)} von {total} Layern exportiert.",
                Qgis.MessageLevel.Success,
            )
        elif successes and failures:
            detail = "\n".join(f"• {name}: {err}" for name, err in failures)
            self._show_error_alert(
                title,
                f"{len(successes)} von {total} Layern erfolgreich, {len(failures)} fehlgeschlagen",
                detail,
            )
        elif failures:
            detail = "\n".join(f"• {name}: {err}" for name, err in failures)
            self._show_error_alert(title, "Keiner der Layer konnte exportiert werden", detail)
        else:
            self.iface.messageBar().pushMessage(title, "Abgebrochen.", Qgis.MessageLevel.Warning)

    def _export_portable_package(self):
        """Menüpunkt-Handler: Dialog öffnen und Export durchführen."""
        PortableExporter(
            self.plugin_dir,
            get_layer=lambda: self.layer,
            on_success=lambda zip_path: self.iface.messageBar().pushMessage(
                "Erfolg", f"Portables Paket wurde erstellt: {zip_path}", Qgis.MessageLevel.Info
            ),
            on_error=self._show_error_alert,
        ).prompt_and_export(self.iface.mainWindow())


def _safe_filename(name: str) -> str:
    keep = "-_. "
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()
    return cleaned or "layer"


class _LayerPickDialog(QDialog):
    """Checkbox list of vector layers. Returns chosen layers on accept."""

    def __init__(self, parent, title: str, layers: list[QgsVectorLayer], active_id: str | None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._layers = layers

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Layer zum Exportieren wählen (Mehrfachauswahl möglich):"))

        self._list = QListWidget(self)
        for lyr in layers:
            item = QListWidgetItem(lyr.name())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if lyr.id() == active_id else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        # Quick selection helpers — saves clicking when the user wants all/none.
        btn_row = QVBoxLayout()
        select_all = QPushButton("Alle auswählen", self)
        select_all.clicked.connect(self._select_all)
        select_none = QPushButton("Keine auswählen", self)
        select_none.clicked.connect(self._select_none)
        btn_row.addWidget(select_all)
        btn_row.addWidget(select_none)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(380, 420)

    def _select_all(self):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)

    def _select_none(self):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def chosen(self) -> list[QgsVectorLayer]:
        result = []
        for i, lyr in enumerate(self._layers):
            if self._list.item(i).checkState() == Qt.CheckState.Checked:
                result.append(lyr)
        return result

    @classmethod
    def pick(
        cls,
        parent,
        title: str,
        layers: list[QgsVectorLayer],
        active_id: str | None,
    ) -> list[QgsVectorLayer] | None:
        dlg = cls(parent, title, layers, active_id)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        chosen = dlg.chosen()
        return chosen or None
