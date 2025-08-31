import os
from PyQt5.QtCore import Qt, QSize, QEvent, QObject, QVariant
from PyQt5.QtGui import QIcon, QDrag, QPixmap
from PyQt5.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QDialog,
    QPushButton, QInputDialog, QHBoxLayout, QSlider, QCheckBox
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField,
    QgsFeature, QgsGeometry, QgsPointXY,
    QgsMarkerSymbol, QgsSvgMarkerSymbolLayer,
    QgsVectorFileWriter, QgsProperty, QgsSingleSymbolRenderer,
    QgsSymbolLayer, QgsFeatureRequest, QgsRendererCategory, QgsCategorizedSymbolRenderer, QgsUnitTypes, QgsMapLayer
)
import time
from qgis.PyQt.QtCore import QVariant
from qgis.utils import iface
from qgis.gui import QgsMapTool, QgsMapToolIdentify
from .identifytool import FeatureDock
from .thwtoolboxplugin_dock import SvgDock


class CanvasDropFilter(QObject):
    def __init__(self, canvas, place_cb):
        super().__init__(canvas)
        self.canvas = canvas
        self.place_cb = place_cb

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.DragEnter:
            if ev.mimeData().hasText():
                ev.acceptProposedAction()
                return True
        if ev.type() == QEvent.Drop:
            svg = ev.mimeData().text()
            pt = self.canvas.getCoordinateTransform().toMapCoordinates(ev.pos().x(), ev.pos().y())
            self.place_cb(svg, QgsPointXY(pt))
            ev.acceptProposedAction()
            return True
        return False


class IdentifyTool(QgsMapToolIdentify):
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.ArrowCursor)
        
        # Dock-Widget erstellen
        self.feature_dock = FeatureDock(layer_manager.iface.mainWindow())
        layer_manager.iface.addDockWidget(Qt.RightDockWidgetArea, self.feature_dock)

    def canvasReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        try:
            # Konvertiere Mausposition zu Kartenkoordinaten
            point = self.canvas.getCoordinateTransform().toMapCoordinates(ev.pos().x(), ev.pos().y())
            
            # Erstelle einen Feature-Request
            request = QgsFeatureRequest()
            request.setFilterRect(self.canvas.mapSettings().mapToLayerCoordinates(self.layer, self.canvas.extent()))
            
            # Suche nach Features in der Nähe des Klickpunkts
            closest_feature = None
            min_distance = float('inf')
            
            for feature in self.layer.getFeatures(request):
                if feature.geometry():
                    distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
                    if distance < min_distance and distance < 10:  # 10 Map Units als Toleranz
                        min_distance = distance
                        closest_feature = feature
            
            if closest_feature:
                self.feature_dock.show_feature(closest_feature, self.layer_manager)
                # Aktualisiere auch den MoveTool, falls vorhanden
                if hasattr(self.layer_manager, 'move_tool'):
                    self.layer_manager.move_tool.moving_feature = closest_feature
            else:
                self.feature_dock.hide()
                
        except Exception as e:
            print(f"Fehler beim Identifizieren: {str(e)}")


class MoveTool(QgsMapTool):
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.moving_feature = None
        self.is_move_mode = False
        self.setCursor(Qt.ArrowCursor)
        self.pan_start = None
        self.is_panning = False
        self.last_center = None
        self.last_pos = None
        self.update_timer = None
        self.last_update_time = 0
        self.update_interval = 100 # ms
        self.update_threshold = 0.1 # Map Units
        self.is_editing = False
        self.last_canvas_update = 0
        self.last_dock_update = 0

    def set_move_mode(self, enabled):
        self.is_move_mode = enabled
        if enabled:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.moving_feature = None

    def canvasMoveEvent(self, event):
        # Prüfe, ob der Cursor über einem Feature ist (nur alle 100ms wenn nicht im Move-Modus)
        if not self.moving_feature:
            current_time = time.time() * 1000  # Konvertiere zu Millisekunden
            if current_time - self.last_update_time > 100:  # Nur alle 100ms Feature-Suche
                point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
                request = QgsFeatureRequest()
                request.setFilterRect(self.canvas.mapSettings().mapToLayerCoordinates(self.layer, self.canvas.extent()))
                
                closest_feature = None
                min_distance = float('inf')
                
                for feature in self.layer.getFeatures(request):
                    if feature.geometry():
                        distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
                        if distance < min_distance and distance < 10:  # 10 Map Units als Toleranz
                            min_distance = distance
                            closest_feature = feature
                
                if closest_feature:
                    self.setCursor(Qt.PointingHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
                
                self.last_update_time = current_time
        else:
            # Wenn im Move-Modus, Cursor entsprechend setzen
            self.setCursor(Qt.ClosedHandCursor)

        if self.moving_feature:
            # Aktualisiere die Position des Features mit verbessertem Throttling
            point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
            current_time = time.time() * 1000  # Konvertiere zu Millisekunden
            
            # Nur aktualisieren, wenn sich die Position signifikant geändert hat und genug Zeit vergangen ist
            should_update = False
            if self.last_pos is None:
                should_update = True
            elif point.distance(self.last_pos) > self.update_threshold:
                if current_time - self.last_update_time > self.update_interval:
                    should_update = True
            
            if should_update:
                # Layer nur einmal in den Edit-Modus versetzen
                if not self.is_editing:
                    self.layer.startEditing()
                    self.is_editing = True
                
                # Feature-Position aktualisieren
                self.layer.changeGeometry(self.moving_feature.id(), QgsGeometry.fromPointXY(point))
                self.last_pos = point
                self.last_update_time = current_time
                
                # Koordinaten im Dock nur alle 300ms aktualisieren
                if current_time - self.last_dock_update > 300:
                    if hasattr(self.layer_manager, 'ident_tool') and hasattr(self.layer_manager.ident_tool, 'feature_dock'):
                        feature = self.layer.getFeature(self.moving_feature.id())
                        if feature.isValid():
                            self.layer_manager.ident_tool.feature_dock.show_feature(feature, self.layer_manager)
                        self.last_dock_update = current_time
                
                # Canvas nur alle 150ms aktualisieren
                if current_time - self.last_canvas_update > 150:
                    self.canvas.refresh()
                    self.last_canvas_update = current_time
        elif not self.is_panning and self.pan_start and self.last_center:
            # Normales Pan-Verhalten
            dx = event.pos().x() - self.pan_start.x()
            dy = event.pos().y() - self.pan_start.y()
            
            # Berechne neue Kartenmitte
            map_units_per_pixel = self.canvas.mapUnitsPerPixel()
            new_center_x = self.last_center.x() - (dx * map_units_per_pixel)
            new_center_y = self.last_center.y() + (dy * map_units_per_pixel)
            
            self.canvas.setCenter(QgsPointXY(new_center_x, new_center_y))
            self.canvas.refresh()

    def canvasPressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        # Konvertiere Mausposition zu Kartenkoordinaten
        point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
        self.last_pos = point
        
        # Suche nach dem nächsten Feature
        request = QgsFeatureRequest()
        request.setFilterRect(self.canvas.mapSettings().mapToLayerCoordinates(self.layer, self.canvas.extent()))
        
        closest_feature = None
        min_distance = float('inf')
        
        for feature in self.layer.getFeatures(request):
            if feature.geometry():
                distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
                if distance < min_distance and distance < 10:  # 10 Map Units als Toleranz
                    min_distance = distance
                    closest_feature = feature
        
        if closest_feature:
            self.moving_feature = closest_feature
            self.setCursor(Qt.ClosedHandCursor)
            # Zeige Feature-Details an
            if hasattr(self.layer_manager, 'ident_tool') and hasattr(self.layer_manager.ident_tool, 'feature_dock'):
                self.layer_manager.ident_tool.feature_dock.show_feature(closest_feature, self.layer_manager)
        else:
            # Kein Feature gefunden, starte Panning
            self.pan_start = event.pos()
            self.is_panning = True
            self.last_center = self.canvas.center()

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.moving_feature:
                # Layer-Änderungen committen wenn im Edit-Modus
                if self.is_editing:
                    self.layer.commitChanges()
                    self.is_editing = False
                
                self.moving_feature = None
                self.last_pos = None
                self.setCursor(Qt.PointingHandCursor)
            elif self.is_panning:
                # Normales Pan-Verhalten
                self.is_panning = False
                self.pan_start = None
                self.last_center = None


class THWToolboxPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.layer = None
        self.current_svg = None
        self.drop_filter = None
        self.ident_tool = None
        self.move_tool = None
        self.action = None
        self.dock = None

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "icon.svg"))
        self.action = QAction(icon, "THW Toolbox", self.iface.mainWindow())
        self.action.triggered.connect(self.activate)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("THW Toolbox", self.action)

    def unload(self):
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

    def activate(self):
        self._init_layer()
        self._init_dock()
        # Drag & Drop
        if not self.drop_filter:
            df = CanvasDropFilter(self.canvas, self._place_feature)
            self.drop_filter = df
            self.canvas.viewport().installEventFilter(df)
            self.canvas.setAcceptDrops(True)
        # IdentifyTool
        if not self.ident_tool:
            self.ident_tool = IdentifyTool(self.canvas, self)
        # MoveTool
        if not self.move_tool:
            self.move_tool = MoveTool(self.canvas, self)
        self.canvas.setMapTool(self.move_tool)

    def _init_layer(self):
        proj = QgsProject.instance()
        pfile = proj.fileName()
        
        # Prüfe, ob der Layer bereits im Projekt existiert
        existing_layers = QgsProject.instance().mapLayersByName("THW Toolbox Marker")
        if existing_layers:
            self.layer = existing_layers[0]
            return
        
        # Erstelle immer einen persistenten Layer, auch wenn kein Projekt gespeichert ist
        crs = self.canvas.mapSettings().destinationCrs().authid()
        
        # Bestimme den Speicherort für die GeoPackage
        if pfile:
            # Wenn Projekt gespeichert ist, verwende den Projektpfad
            base = os.path.splitext(pfile)[0] + "_taktischezeichen"
        else:
            # Wenn kein Projekt gespeichert ist, verwende das Plugin-Verzeichnis
            base = os.path.join(self.plugin_dir, "default_taktischezeichen")
        
        gpkg = base + ".gpkg"
        lname = "taktische_zeichen"

        # Erstelle oder lade die GeoPackage
        if os.path.exists(gpkg):
            uri = f"{gpkg}|layername={lname}"
            lyr = QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")
            
            # Prüfen ob die Felder existieren
            existing_fields = [field.name() for field in lyr.fields()]
            if "scale_with_map" not in existing_fields or "svg_content" not in existing_fields:
                # Layer mit neuen Feldern aktualisieren
                self._update_layer_fields(lyr, gpkg, lname)
                lyr = QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")
        else:
            # Erstelle neue GeoPackage mit allen Feldern
            lyr = self._create_new_layer(gpkg, lname, crs)
        
        # Renderer initialisieren
        self._init_renderer(lyr)
        QgsProject.instance().addMapLayer(lyr)
        self.layer = lyr

    def _init_dock(self):
        if self.dock:
            return
        self.dock = QDockWidget("Taktische Zeichen", self.iface.mainWindow())
        self.dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.svg_dock_widget = SvgDock(self.plugin_dir, self._on_svg_drag_start)
        self.dock.setWidget(self.svg_dock_widget)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)

    def _on_svg_drag_start(self, svg_path):
        self.current_svg = svg_path

    def _create_new_layer(self, gpkg, lname, crs):
        """Erstellt einen neuen Layer mit allen erforderlichen Feldern."""
        # Stelle sicher, dass das Verzeichnis existiert
        os.makedirs(os.path.dirname(gpkg), exist_ok=True)
        
        # Erstelle temporären Memory-Layer
        mem = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
        dp = mem.dataProvider()
        dp.addAttributes([
            QgsField("name", QVariant.String),
            QgsField("svg_path", QVariant.String),
            QgsField("svg_content", QVariant.String),
            QgsField("size", QVariant.Double),
            QgsField("scale_with_map", QVariant.Bool),
        ])
        mem.updateFields()
        
        # Speichere als GeoPackage
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = lname
        QgsVectorFileWriter.writeAsVectorFormatV2(mem, gpkg, QgsProject.instance().transformContext(), opts)
        
        # Lade den gespeicherten Layer
        uri = f"{gpkg}|layername={lname}"
        return QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")

    def _update_layer_fields(self, old_layer, gpkg, lname):
        """Aktualisiert einen bestehenden Layer mit neuen Feldern."""
        crs = self.canvas.mapSettings().destinationCrs().authid()
        mem = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
        dp = mem.dataProvider()
        dp.addAttributes([
            QgsField("name", QVariant.String),
            QgsField("svg_path", QVariant.String),
            QgsField("svg_content", QVariant.String),
            QgsField("size", QVariant.Double),
            QgsField("scale_with_map", QVariant.Bool),
        ])
        mem.updateFields()
        
        # Features kopieren
        existing_fields = [field.name() for field in old_layer.fields()]
        for feat in old_layer.getFeatures():
            new_feat = QgsFeature(mem.fields())
            new_feat.setGeometry(feat.geometry())
            new_feat.setAttribute("name", feat["name"])
            new_feat.setAttribute("svg_path", feat["svg_path"])
            svg_content = feat["svg_content"] if "svg_content" in existing_fields else ""
            new_feat.setAttribute("svg_content", svg_content)
            new_feat.setAttribute("size", feat["size"])
            new_feat.setAttribute("scale_with_map", feat["scale_with_map"] if "scale_with_map" in existing_fields else False)
            mem.dataProvider().addFeature(new_feat)
        
        # Alten Layer entfernen und neuen speichern
        QgsProject.instance().removeMapLayer(old_layer)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = lname
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        QgsVectorFileWriter.writeAsVectorFormatV2(mem, gpkg, QgsProject.instance().transformContext(), opts)

    def _init_renderer(self, layer):
        """Initialisiert den Renderer für den Layer."""
        categories = []
        for feat in layer.getFeatures():
            svg_path = feat["svg_path"]
            svg_content = feat["svg_content"] if "svg_content" in [field.name() for field in layer.fields()] else ""
            size = feat["size"]
            scale_with_map = feat["scale_with_map"]
            sym = QgsMarkerSymbol.createSimple({})
            
            # Verwende SVG-Inhalt falls verfügbar, sonst SVG-Pfad
            if svg_content and svg_content.strip():
                temp_svg = self._create_temp_svg_from_content(svg_content, feat.id())
                ly = QgsSvgMarkerSymbolLayer(temp_svg, size, 0)
            else:
                ly = QgsSvgMarkerSymbolLayer(svg_path, size, 0)
            
            if not scale_with_map:
                ly.setSizeUnit(QgsUnitTypes.RenderMapUnits)
            sym.changeSymbolLayer(0, ly)
            cat = QgsRendererCategory(svg_path, sym, svg_path)
            categories.append(cat)
        
        renderer = QgsCategorizedSymbolRenderer("svg_path", categories)
        layer.setRenderer(renderer)

    def _save_layer(self):
        """Speichert den aktuellen Layer in die GeoPackage-Datei."""
        # Der Layer ist bereits persistent, nur speichern
        if self.layer.providerType() == "ogr":
            # Layer ist bereits eine GeoPackage, nichts zu tun
            return
        
        # Falls es doch ein Memory-Layer ist, speichern
        proj = QgsProject.instance()
        pfile = proj.fileName()
        
        if not pfile:
            # Verwende Plugin-Verzeichnis als Fallback
            base = os.path.join(self.plugin_dir, "default_taktischezeichen")
        else:
            base = os.path.splitext(pfile)[0] + "_taktischezeichen"
        
        gpkg = base + ".gpkg"
        lname = "taktische_zeichen"
        
        # Stelle sicher, dass das Verzeichnis existiert
        os.makedirs(os.path.dirname(gpkg), exist_ok=True)
        
        # Speichere den Layer
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = lname
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        
        result = QgsVectorFileWriter.writeAsVectorFormatV2(
            self.layer, gpkg, QgsProject.instance().transformContext(), opts
        )
        
        if result[0] != QgsVectorFileWriter.NoError:
            self.iface.messageBar().pushMessage(
                "Fehler",
                f"Konnte Layer nicht speichern: {result[1]}",
                level=3  # Critical level
            )
            return
        
        # Lade den gespeicherten Layer aus der Datei
        uri = f"{gpkg}|layername={lname}"
        lyr = QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")
        QgsProject.instance().addMapLayer(lyr)
        self.layer = lyr
        
        # Aktualisiere Referenzen in Tools
        if hasattr(self, 'ident_tool'):
            self.ident_tool.layer = self.layer
        if hasattr(self, 'move_tool'):
            self.move_tool.layer = self.layer

    def _create_temp_svg_from_content(self, svg_content, feature_id):
        """Erstellt eine temporäre SVG-Datei aus dem gespeicherten SVG-Inhalt."""
        import tempfile
        
        # Erstelle temporäres Verzeichnis falls es nicht existiert
        temp_dir = os.path.join(self.plugin_dir, "temp_svg")
        os.makedirs(temp_dir, exist_ok=True)
        
        # Erstelle eindeutigen Dateinamen
        temp_filename = f"temp_svg_{feature_id}.svg"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        try:
            # Schreibe SVG-Inhalt in temporäre Datei
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(svg_content)
            return temp_path
        except Exception as e:
            print(f"Fehler beim Erstellen der temporären SVG-Datei: {str(e)}")
            return None

    def _place_feature(self, svg_path, point):
        # Felder überprüfen und ggf. hinzufügen
        required_fields = {
            "name": QVariant.String,
            "svg_path": QVariant.String,
            "svg_content": QVariant.String,  # Neues Feld für SVG-Inhalt
            "size": QVariant.Double,
            "scale_with_map": QVariant.Bool
        }
        
        existing_fields = {field.name(): field.type() for field in self.layer.fields()}
        
        # Fehlende Felder hinzufügen
        fields_to_add = []
        for field_name, field_type in required_fields.items():
            if field_name not in existing_fields:
                fields_to_add.append(QgsField(field_name, field_type))
        
        if fields_to_add:
            self.layer.startEditing()
            for field in fields_to_add:
                self.layer.addAttribute(field)
            self.layer.commitChanges()
        
        # SVG-Inhalt lesen
        svg_content = ""
        try:
            if os.path.exists(svg_path):
                with open(svg_path, 'r', encoding='utf-8') as f:
                    svg_content = f.read()
        except Exception as e:
            print(f"Fehler beim Lesen der SVG-Datei: {str(e)}")
        
        # Feature erstellen
        f = QgsFeature(self.layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(point))
        f.setAttribute("name", os.path.basename(svg_path))
        f.setAttribute("svg_path", svg_path)
        f.setAttribute("svg_content", svg_content)  # SVG-Inhalt speichern
        f.setAttribute("size", 30.0)  # Standardgröße auf 30 setzen
        f.setAttribute("scale_with_map", False)  # Standardmäßig nicht mit Karte skalieren
        
        # Feature zum Layer hinzufügen
        self.layer.startEditing()
        self.layer.dataProvider().addFeature(f)
        self.layer.commitChanges()
        self.layer.updateExtents()
        
        # Layer speichern
        self._save_layer()
        
        # Renderer aktualisieren
        categories = []
        for feat in self.layer.getFeatures():
            svg_path_feat = feat["svg_path"]
            svg_content_feat = feat["svg_content"] if "svg_content" in [field.name() for field in self.layer.fields()] else ""
            size = feat["size"]
            scale_with_map = feat["scale_with_map"]
            sym = QgsMarkerSymbol.createSimple({})
            
            # Verwende SVG-Inhalt falls verfügbar, sonst SVG-Pfad
            if svg_content_feat and svg_content_feat.strip():
                # Erstelle temporäre SVG-Datei aus Inhalt
                temp_svg = self._create_temp_svg_from_content(svg_content_feat, feat.id())
                ly = QgsSvgMarkerSymbolLayer(temp_svg, size, 0)
            else:
                ly = QgsSvgMarkerSymbolLayer(svg_path_feat, size, 0)
            
            if not scale_with_map:
                ly.setSizeUnit(QgsUnitTypes.RenderMapUnits)
            sym.changeSymbolLayer(0, ly)
            cat = QgsRendererCategory(svg_path_feat, sym, svg_path_feat)
            categories.append(cat)
        
        renderer = QgsCategorizedSymbolRenderer("svg_path", categories)
        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()

    # Identify callbacks
    def delete_feature(self, fid):
        """Löscht ein Feature und aktualisiert den Layer."""
        # Prüfe, ob der Layer eine GeoPackage ist
        if self.layer.providerType() == "ogr":
            # Direkt aus der GeoPackage löschen
            self.layer.startEditing()
            self.layer.deleteFeature(fid)
            self.layer.commitChanges()
            
            # Renderer aktualisieren
            self._init_renderer(self.layer)
            self.layer.triggerRepaint()
        else:
            # Fallback für Memory-Layer
            self._delete_feature_fallback(fid)
        
        # Nach dem Löschen die Baumstruktur im Dock aktualisieren
        if hasattr(self, 'svg_dock_widget'):
            self.svg_dock_widget.treeWidget.clear()
            self.svg_dock_widget.populate_root_folders()
        
        # Layer-Panel (Layer Tree) aktualisieren
        if hasattr(self.iface, 'layerTreeView'):
            self.iface.layerTreeView().refreshLayerSymbology(self.layer.id())
            
        # Canvas neu zeichnen
        self.canvas.refresh()
        self.canvas.update()

    def _delete_feature_fallback(self, fid):
        """Fallback-Methode für das Löschen von Features in Memory-Layern."""
        crs = self.canvas.mapSettings().destinationCrs().authid()
        
        # Temporären Layer erstellen
        temp_layer = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
        dp = temp_layer.dataProvider()
        
        # Felder hinzufügen
        dp.addAttributes([
            QgsField("name", QVariant.String),
            QgsField("svg_path", QVariant.String),
            QgsField("svg_content", QVariant.String),
            QgsField("size", QVariant.Double),
            QgsField("scale_with_map", QVariant.Bool),
        ])
        temp_layer.updateFields()
        
        # Alle Features außer dem zu löschenden kopieren
        for feat in self.layer.getFeatures():
            if feat.id() != fid:
                new_feat = QgsFeature(temp_layer.fields())
                new_feat.setGeometry(feat.geometry())
                new_feat.setAttribute("name", feat["name"])
                new_feat.setAttribute("svg_path", feat["svg_path"])
                new_feat.setAttribute("svg_content", feat["svg_content"] if "svg_content" in [field.name() for field in self.layer.fields()] else "")
                new_feat.setAttribute("size", feat["size"])
                new_feat.setAttribute("scale_with_map", feat["scale_with_map"])
                temp_layer.dataProvider().addFeature(new_feat)
        
        # Alten Layer aus dem Projekt entfernen
        QgsProject.instance().removeMapLayer(self.layer.id())
        
        # Neuen Layer erstellen und zum Projekt hinzufügen
        self.layer = temp_layer
        QgsProject.instance().addMapLayer(self.layer)
        
        # Renderer neu erstellen
        self._init_renderer(self.layer)
        
        # Layer-Referenzen in anderen Klassen aktualisieren
        if hasattr(self, 'ident_tool'):
            self.ident_tool.layer = self.layer
        if hasattr(self, 'move_tool'):
            self.move_tool.layer = self.layer

    def _create_temp_svg_from_content(self, svg_content, feature_id):
        """Erstellt eine temporäre SVG-Datei aus dem gespeicherten SVG-Inhalt."""
        import tempfile
        
        # Erstelle temporäres Verzeichnis falls es nicht existiert
        temp_dir = os.path.join(self.plugin_dir, "temp_svg")
        os.makedirs(temp_dir, exist_ok=True)
        
        # Erstelle eindeutigen Dateinamen
        temp_filename = f"temp_svg_{feature_id}.svg"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        try:
            # Schreibe SVG-Inhalt in temporäre Datei
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(svg_content)
            return temp_path
        except Exception as e:
            print(f"Fehler beim Erstellen der temporären SVG-Datei: {str(e)}")
            return None

    def resize_feature(self, fid, size):
        idx = self.layer.fields().indexFromName("size")
        self.layer.startEditing()
        self.layer.changeAttributeValue(fid, idx, size)
        self.layer.commitChanges()
        self.layer.triggerRepaint()
        
        # Layer speichern
        self._save_layer()

    def toggle_scale(self, fid, scale_with_map):
        idx = self.layer.fields().indexFromName("scale_with_map")
        self.layer.startEditing()
        self.layer.changeAttributeValue(fid, idx, scale_with_map)
        self.layer.commitChanges()
        
        # Renderer aktualisieren
        categories = []
        for feat in self.layer.getFeatures():
            svg_path_feat = feat["svg_path"]
            svg_content_feat = feat["svg_content"] if "svg_content" in [field.name() for field in self.layer.fields()] else ""
            size = feat["size"]
            scale_with_map = feat["scale_with_map"]
            sym = QgsMarkerSymbol.createSimple({})
            
            # Verwende SVG-Inhalt falls verfügbar, sonst SVG-Pfad
            if svg_content_feat and svg_content_feat.strip():
                # Erstelle temporäre SVG-Datei aus Inhalt
                temp_svg = self._create_temp_svg_from_content(svg_content_feat, feat.id())
                ly = QgsSvgMarkerSymbolLayer(temp_svg, size, 0)
            else:
                ly = QgsSvgMarkerSymbolLayer(svg_path_feat, size, 0)
            
            if not scale_with_map:
                ly.setSizeUnit(QgsUnitTypes.RenderMapUnits)
            sym.changeSymbolLayer(0, ly)
            cat = QgsRendererCategory(svg_path_feat, sym, svg_path_feat)
            categories.append(cat)
        
        renderer = QgsCategorizedSymbolRenderer("svg_path", categories)
        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()
        
        # Layer speichern
        self._save_layer()
