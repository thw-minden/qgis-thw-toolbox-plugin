import os
import logging
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField,
    QgsFeature, QgsGeometry, QgsPointXY,
    QgsVectorFileWriter
)
from PyQt5.QtCore import QVariant

# Logger konfigurieren
logger = logging.getLogger(__name__)

class LayerManager:
    def __init__(self, iface, plugin_dir):
        self.iface      = iface
        self.canvas     = iface.mapCanvas()
        self.plugin_dir = plugin_dir
        self.layer      = None
        self.current_svg= None

    def init_layer(self):
        """Lädt oder erzeugt das GeoPackage mit den Feldern name, svg_path, size."""
        try:
            proj = QgsProject.instance()
            path = proj.fileName()
            
            # Wenn kein Projekt gespeichert ist, verwende das Plugin-Verzeichnis
            if not path:
                base = os.path.join(self.plugin_dir, "svgmarkers")
                logger.info(f"Kein Projekt gespeichert, verwende Plugin-Verzeichnis: {base}")
            else:
                base = os.path.splitext(path)[0] + "_svgmarkers"
                logger.info(f"Projekt gefunden, verwende: {base}")
            
            gpkg = base + ".gpkg"
            name = "svg_markers"
            
            # Stelle sicher, dass das Verzeichnis existiert
            os.makedirs(os.path.dirname(gpkg), exist_ok=True)
            
            if os.path.exists(gpkg):
                logger.info(f"GeoPackage existiert bereits: {gpkg}")
                uri = f"{gpkg}|layername={name}"
                self.layer = QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")
            else:
                logger.info(f"Erstelle neues GeoPackage: {gpkg}")
                crs = self.canvas.mapSettings().destinationCrs().authid()
                mem = QgsVectorLayer(f"Point?crs={crs}", "temp", "memory")
                prov = mem.dataProvider()
                prov.addAttributes([
                    QgsField("name", QVariant.String),
                    QgsField("svg_path", QVariant.String),
                    QgsField("size", QVariant.Double),
                ])
                mem.updateFields()
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName = "GPKG"
                opts.layerName  = name
                
                # Versuche die Datei zu erstellen
                result = QgsVectorFileWriter.writeAsVectorFormatV2(
                    mem, gpkg, QgsProject.instance().transformContext(), opts
                )
                
                if result[0] != QgsVectorFileWriter.NoError:
                    logger.error(f"Fehler beim Erstellen der GeoPackage-Datei: {result[1]}")
                    raise Exception(f"Konnte GeoPackage nicht erstellen: {result[1]}")
                
                uri = f"{gpkg}|layername={name}"
                self.layer = QgsVectorLayer(uri, "THW Toolbox Marker", "ogr")
            
            if self.layer and not QgsProject.instance().mapLayersByName("THW Toolbox Marker"):
                QgsProject.instance().addMapLayer(self.layer)
                logger.info("Layer zum Projekt hinzugefügt")
                
        except Exception as e:
            logger.error(f"Fehler in init_layer: {str(e)}")
            raise

    def add_feature(self, svg_path, point):
        feat = QgsFeature(self.layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(point))
        feat.setAttribute("name", os.path.basename(svg_path))
        feat.setAttribute("svg_path", svg_path)
        feat.setAttribute("size", 10.0)
        self.layer.dataProvider().addFeature(feat)
        self.layer.updateExtents()
        self.layer.triggerRepaint()

    def delete_feature(self, fid):
        self.layer.dataProvider().deleteFeatures([fid])
        self.layer.triggerRepaint()

    def resize_feature(self, fid, size):
        caps = self.layer.dataProvider().capabilities()
        if caps & QgsVectorDataProvider.ChangeAttributeValues:
            idx = self.layer.fields().indexFromName("size")
            self.layer.startEditing()
            self.layer.changeAttributeValue(fid, idx, size)
            self.layer.commitChanges()
            self.layer.triggerRepaint()

    def remove(self):
        if self.layer:
            QgsProject.instance().removeMapLayer(self.layer.id())
