# identifytool.py

import os
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QPushButton, QInputDialog, 
                           QLabel, QHBoxLayout, QDockWidget, QWidget, QSlider,
                           QCheckBox, QSpinBox)
from PyQt5.QtGui import QPixmap
from qgis.gui import QgsMapToolIdentify
from qgis.core import QgsFeatureRequest, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

class FeatureDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Marker Details", parent)
        self.setAllowedAreas(Qt.RightDockWidgetArea)
        
        # Hauptwidget für den Inhalt
        self.content_widget = QWidget()
        self.setWidget(self.content_widget)
        self.main_layout = QVBoxLayout(self.content_widget)
        
        # SVG-Anzeige
        self.svg_label = QLabel()
        self.svg_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.svg_label)
        
        # Koordinaten-Anzeige
        self.coord_label = QLabel("Koordinaten: ")
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("QLabel { font-weight: bold; color: #2E86AB; }")
        self.main_layout.addWidget(self.coord_label)
        
        # UTM 32N Koordinaten
        self.utm32n_label = QLabel("")
        self.utm32n_label.setAlignment(Qt.AlignCenter)
        self.utm32n_label.setStyleSheet("QLabel { color: #2E86AB; font-size: 11px; }")
        self.main_layout.addWidget(self.utm32n_label)
        
        # UTM 32U Koordinaten
        self.utm32u_label = QLabel("")
        self.utm32u_label.setAlignment(Qt.AlignCenter)
        self.utm32u_label.setStyleSheet("QLabel { color: #2E86AB; font-size: 11px; }")
        self.main_layout.addWidget(self.utm32u_label)
        
        # MGRS/UTMREF Koordinaten
        self.mgrs_label = QLabel("")
        self.mgrs_label.setAlignment(Qt.AlignCenter)
        self.mgrs_label.setStyleSheet("QLabel { color: #2E86AB; font-size: 11px; }")
        self.main_layout.addWidget(self.mgrs_label)
        
        # Längen- und Breitengrad
        self.latlon_label = QLabel("")
        self.latlon_label.setAlignment(Qt.AlignCenter)
        self.latlon_label.setStyleSheet("QLabel { color: #2E86AB; font-size: 11px; }")
        self.main_layout.addWidget(self.latlon_label)
        
        # Zusätzliche Koordinaten-Informationen
        self.coord_info_label = QLabel("")
        self.coord_info_label.setAlignment(Qt.AlignCenter)
        self.coord_info_label.setStyleSheet("QLabel { color: #666; font-size: 10px; }")
        self.main_layout.addWidget(self.coord_info_label)
        
        # Größen-SpinBox
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Größe:"))
        
        self.size_spinbox = QSpinBox()
        self.size_spinbox.setMinimum(10)  # Minimale Größe
        self.size_spinbox.setMaximum(200)  # Maximale Größe
        self.size_spinbox.setValue(50)  # Standardwert
        self.size_spinbox.setSingleStep(1)  # Schrittweite
        size_layout.addWidget(self.size_spinbox)
        
        self.main_layout.addLayout(size_layout)
        
        # Skalierungs-Checkbox
        self.scale_checkbox = QCheckBox("Mit Karte skalieren")
        self.main_layout.addWidget(self.scale_checkbox)
        
        # Buttons in horizontalem Layout
        self.button_layout = QHBoxLayout()
        
        self.btn_delete = QPushButton("Löschen")
        self.button_layout.addWidget(self.btn_delete)
        
        self.main_layout.addLayout(self.button_layout)
        
        # Initial verstecken
        self.hide()
        
    def convert_to_utm32n(self, point, source_crs):
        """Konvertiert Koordinaten zu UTM Zone 32N (EPSG:32632)"""
        try:
            # UTM Zone 32N CRS (EPSG:32632)
            utm_crs = QgsCoordinateReferenceSystem("EPSG:32632")
            
            # Koordinatentransformation erstellen
            transform = QgsCoordinateTransform(source_crs, utm_crs, QgsProject.instance())
            
            # Koordinaten transformieren
            utm_point = transform.transform(point)
            
            # Formatierung der UTM-Koordinaten
            easting = int(utm_point.x())
            northing = int(utm_point.y())
            
            return f"UTM 32N: {easting}E {northing}N"
        except Exception as e:
            return f"UTM 32N: Fehler"
    
    def convert_to_utm32u(self, point, source_crs):
        """Konvertiert Koordinaten zu UTM Zone 32U (EPSG:32632) - Südhemisphäre"""
        try:
            # UTM Zone 32U CRS (EPSG:32632) - für Südhemisphäre
            utm_crs = QgsCoordinateReferenceSystem("EPSG:32632")
            
            # Koordinatentransformation erstellen
            transform = QgsCoordinateTransform(source_crs, utm_crs, QgsProject.instance())
            
            # Koordinaten transformieren
            utm_point = transform.transform(point)
            
            # Formatierung der UTM-Koordinaten (Südhemisphäre)
            easting = int(utm_point.x())
            northing = int(utm_point.y())
            
            return f"UTM 32U: {easting}E {northing}S"
        except Exception as e:
            return f"UTM 32U: Fehler"
    
    def convert_to_mgrs(self, point, source_crs):
        """Konvertiert Koordinaten zu MGRS/UTMREF Format"""
        try:
            # Zuerst zu UTM Zone 32N
            utm_crs = QgsCoordinateReferenceSystem("EPSG:32632")
            transform_to_utm = QgsCoordinateTransform(source_crs, utm_crs, QgsProject.instance())
            utm_point = transform_to_utm.transform(point)
            
            # MGRS Format: 32U + 100km Grid + Easting/Northing
            easting = int(utm_point.x())
            northing = int(utm_point.y())
            
            # 100km Grid berechnen (MGRS verwendet spezielle Grid-Referenzen)
            # Für Zone 32U (Deutschland) verwenden wir die Standard-Grid-Referenzen
            grid_e = (easting // 100000) % 10
            grid_n = (northing // 100000) % 10
            
            # Easting/Northing innerhalb des 100km Grids (5-stellig)
            easting_100k = easting % 100000
            northing_100k = northing % 100000
            
            # MGRS Format: 32U + Grid + Easting + Northing
            mgrs_text = f"32U {grid_e}{grid_n} {easting_100k:05d} {northing_100k:05d}"
            
            return f"MGRS: {mgrs_text}"
        except Exception as e:
            return f"MGRS: Fehler"
    
    def convert_to_latlon(self, point, source_crs):
        """Konvertiert Koordinaten zu Längen- und Breitengrad (WGS84)"""
        try:
            # WGS84 CRS (EPSG:4326)
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            
            # Koordinatentransformation erstellen
            transform = QgsCoordinateTransform(source_crs, wgs84_crs, QgsProject.instance())
            
            # Koordinaten transformieren
            wgs84_point = transform.transform(point)
            
            # Formatierung der Koordinaten
            lat = wgs84_point.y()
            lon = wgs84_point.x()
            
            # Himmelsrichtungen bestimmen
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            
            # Absolute Werte
            lat_abs = abs(lat)
            lon_abs = abs(lon)
            
            # Grad, Minuten, Sekunden Format
            lat_deg = int(lat_abs)
            lat_min = int((lat_abs - lat_deg) * 60)
            lat_sec = ((lat_abs - lat_deg - lat_min/60) * 3600)
            
            lon_deg = int(lon_abs)
            lon_min = int((lon_abs - lon_deg) * 60)
            lon_sec = ((lon_abs - lon_deg - lon_min/60) * 3600)
            
            lat_text = f"{lat_deg}°{lat_min:02d}'{lat_sec:05.2f}\"{lat_dir}"
            lon_text = f"{lon_deg}°{lon_min:02d}'{lon_sec:05.2f}\"{lon_dir}"
            
            return f"Lat: {lat_text}\nLon: {lon_text}"
        except Exception as e:
            return f"Lat/Lon: Fehler"
    
    def get_coordinate_info(self, point, source_crs):
        """Gibt zusätzliche Koordinaten-Informationen zurück"""
        try:
            # Ursprüngliche Koordinaten im Quell-CRS
            source_name = source_crs.description() or source_crs.authid()
            
            # Versuche auch WGS84 Koordinaten zu bekommen
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            transform_to_wgs84 = QgsCoordinateTransform(source_crs, wgs84_crs, QgsProject.instance())
            wgs84_point = transform_to_wgs84.transform(point)
            
            # Formatiere die Informationen
            info_text = f"Quell-CRS: {source_name}\n"
            info_text += f"WGS84: {wgs84_point.y():.6f}°N, {wgs84_point.x():.6f}°E"
            
            return info_text
        except Exception as e:
            return f"Quell-CRS: {source_crs.authid()}"
        
    def show_feature(self, feat, layer_manager):
        self.feat = feat
        self.layer_manager = layer_manager
        
        # SVG aktualisieren
        pixmap = QPixmap(feat['svg_path'])
        scaled_pixmap = pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.svg_label.setPixmap(scaled_pixmap)
        
        # Koordinaten anzeigen
        if feat.geometry():
            point = feat.geometry().asPoint()
            source_crs = layer_manager.layer.crs()
            
            # Alle Koordinatenformate berechnen und anzeigen
            utm32n_text = self.convert_to_utm32n(point, source_crs)
            utm32u_text = self.convert_to_utm32u(point, source_crs)
            mgrs_text = self.convert_to_mgrs(point, source_crs)
            latlon_text = self.convert_to_latlon(point, source_crs)
            
            # Labels aktualisieren
            self.coord_label.setText("Koordinaten in verschiedenen Formaten:")
            self.utm32n_label.setText(utm32n_text)
            self.utm32u_label.setText(utm32u_text)
            self.mgrs_label.setText(mgrs_text)
            self.latlon_label.setText(latlon_text)
            
            # Zusätzliche Koordinaten-Informationen anzeigen
            coord_info = self.get_coordinate_info(point, source_crs)
            self.coord_info_label.setText(coord_info)
            
            # Dock-Titel mit UTM 32N Koordinaten aktualisieren
            self.setWindowTitle(f"Marker Details - {utm32n_text}")
        
        # Slider auf aktuelle Größe setzen
        current_size = feat["size"]
        self.size_spinbox.setValue(int(current_size))
        
        # Checkbox auf aktuellen Wert setzen oder Standardwert verwenden
        try:
            scale_with_map = feat["scale_with_map"]
        except KeyError:
            scale_with_map = False
        self.scale_checkbox.setChecked(scale_with_map)
        
        # Buttons, SpinBox und Checkbox neu verbinden
        self.btn_delete.clicked.disconnect() if self.btn_delete.receivers(self.btn_delete.clicked) > 0 else None
        self.size_spinbox.valueChanged.disconnect() if self.size_spinbox.receivers(self.size_spinbox.valueChanged) > 0 else None
        self.scale_checkbox.stateChanged.disconnect() if self.scale_checkbox.receivers(self.scale_checkbox.stateChanged) > 0 else None
        
        self.btn_delete.clicked.connect(self.on_delete)
        self.size_spinbox.valueChanged.connect(self.on_size_change)
        self.scale_checkbox.stateChanged.connect(self.on_scale_toggle)
        
        self.show()
        
    def on_delete(self):
        self.layer_manager.delete_feature(self.feat.id())
        self.hide()
        
    def on_size_change(self, value):
        self.layer_manager.resize_feature(self.feat.id(), value)
        
    def on_scale_toggle(self, state):
        self.layer_manager.toggle_scale(self.feat.id(), state == Qt.Checked)
        
    def hideEvent(self, event):
        # Verschieben-Modus deaktivieren wenn Dock geschlossen wird
        if hasattr(self, 'layer_manager') and hasattr(self.layer_manager, 'move_tool'):
            self.layer_manager.move_tool.set_move_mode(False)
            # Cursor zurücksetzen
            if hasattr(self.layer_manager, 'canvas'):
                self.layer_manager.canvas.setCursor(Qt.ArrowCursor)
        super().hideEvent(event)


class IdentifyTool(QgsMapToolIdentify):
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.ArrowCursor)
        
        # Dock-Widget erstellen
        self.feature_dock = FeatureDock(canvas.parent())
        canvas.parent().addDockWidget(Qt.RightDockWidgetArea, self.feature_dock)
        self.feature_dock.hide()  # Initial verstecken

    def canvasReleaseEvent(self, event):
        # nur Linksklick
        if event.button() != Qt.LeftButton:
            return

        # Identifiziere erstes Feature unter Maus mit größerem Suchradius
        search_radius = 10  # Suchradius in Pixeln
        results = self.identify(
            event.x(), 
            event.y(),
            [self.layer],
            QgsMapToolIdentify.TopDownStopAtFirst,
            search_radius
        )
        if not results:
            self.feature_dock.hide()
            if hasattr(self.layer_manager, 'move_tool'):
                self.layer_manager.move_tool.set_move_mode(False)
            return

        feat = results[0].mFeature
        # Feature im Dock anzeigen
        self.feature_dock.show_feature(feat, self.layer_manager)
        
        # Automatisch in den Verschiebe-Modus wechseln
        if hasattr(self.layer_manager, 'move_tool'):
            self.layer_manager.move_tool.set_move_mode(True)
            # Cursor auf ClosedHand ändern, um anzuzeigen, dass das Feature verschiebbar ist
            self.canvas.setCursor(Qt.ClosedHandCursor)
