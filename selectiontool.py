# selectiontool.py

import math
from PyQt5.QtCore import Qt, QPoint, QRect, QTimer
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor
from PyQt5.QtWidgets import QApplication
from qgis.gui import QgsMapTool, QgsMapCanvas
from qgis.core import QgsPointXY, QgsGeometry, QgsFeatureRequest, QgsFeature, QgsMapToPixel

class SelectionTool(QgsMapTool):
    """Tool für die visuelle Auswahl von Features mit Border und Resize-Punkten"""
    
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.ArrowCursor)
        
        # Ausgewähltes Feature
        self.selected_feature = None
        self.selection_bounds = None
        
        # Resize-Punkte
        self.resize_handles = []
        self.active_handle = None
        self.is_resizing = False
        self.resize_start_size = 0
        self.resize_start_pos = None
        
        # Visuelle Eigenschaften
        self.border_color = QColor(0, 0, 255, 200)  # Blau mit Transparenz
        self.handle_color = QColor(0, 0, 255, 255)   # Blau
        self.handle_size = 8  # Größe der Resize-Punkte in Pixeln
        
        # Verbinde Canvas-Events für das Zeichnen
        self.canvas.mapCanvasRefreshed.connect(self._on_canvas_refresh)
        self.canvas.extentsChanged.connect(self._on_canvas_extents_changed)
        
        # Timer für kontinuierliche Aktualisierung
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_overlay)
        self.update_timer.start(100)  # Alle 100ms aktualisieren
        
    def _update_overlay(self):
        """Aktualisiert das Overlay kontinuierlich"""
        if self.selected_feature:
            self._create_temporary_overlay()
        
    def set_selected_feature(self, feature):
        """Setzt das ausgewählte Feature und berechnet die Bounds"""
        self.selected_feature = feature
        if feature and feature.geometry():
            self._calculate_selection_bounds()
            self._create_resize_handles()
        else:
            self.selected_feature = None
            self.selection_bounds = None
            self.resize_handles = []
            # Entferne das Overlay
            if hasattr(self, 'temp_overlay') and self.temp_overlay:
                self.temp_overlay.deleteLater()
                self.temp_overlay = None
        self.canvas.refresh()
        
    def _on_canvas_extents_changed(self):
        """Wird aufgerufen, wenn sich das Canvas-Extent ändert"""
        if self.selected_feature:
            self._calculate_selection_bounds()
            self._create_resize_handles()
            
    def _on_canvas_refresh(self):
        """Wird aufgerufen, wenn der Canvas neu gezeichnet wird"""
        if self.selected_feature:
            self._draw_selection_overlay()
        
    def _draw_selection_overlay(self):
        """Zeichnet die Auswahl-Border und Resize-Punkte direkt auf dem Canvas"""
        if not self.selected_feature or not self.selection_bounds:
            return
            
        # Erstelle ein temporäres Overlay-Widget
        self._create_temporary_overlay()
        
    def _create_temporary_overlay(self):
        """Erstellt ein temporäres Overlay-Widget für die Auswahl"""
        if not self.selected_feature or not self.selection_bounds:
            return
            
        # Entferne das alte Overlay falls vorhanden
        if hasattr(self, 'temp_overlay') and self.temp_overlay:
            self.temp_overlay.deleteLater()
            
        # Erstelle neues Overlay-Widget
        from PyQt5.QtWidgets import QWidget
        self.temp_overlay = QWidget(self.canvas)
        self.temp_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.temp_overlay.setAttribute(Qt.WA_NoSystemBackground, True)
        self.temp_overlay.setWindowFlags(Qt.Widget | Qt.FramelessWindowHint)
        self.temp_overlay.setParent(self.canvas)
        self.temp_overlay.move(0, 0)
        self.temp_overlay.resize(self.canvas.size())
        self.temp_overlay.show()
        self.temp_overlay.raise_()
        
        # Zeichne das Overlay
        self.temp_overlay.paintEvent = self._paint_overlay
        self.temp_overlay.update()
        
    def _paint_overlay(self, event):
        """Zeichnet die Border und Resize-Punkte im Overlay"""
        if not self.selected_feature or not self.selection_bounds:
            return
            
        painter = QPainter(self.temp_overlay)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Zeichne Border
        pen = QPen(self.border_color, 2, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.selection_bounds)
        
        # Zeichne Resize-Punkte
        painter.setPen(QPen(self.handle_color, 1))
        painter.setBrush(QBrush(self.handle_color))
        
        for handle in self.resize_handles:
            painter.drawEllipse(handle.x() - self.handle_size, handle.y() - self.handle_size, 
                              self.handle_size * 2, self.handle_size * 2)
            
        painter.end()
        
    def _calculate_selection_bounds(self):
        """Berechnet die Bounds des ausgewählten Features"""
        if not self.selected_feature or not self.selected_feature.geometry():
            return
            
        # Hole die aktuelle Größe des Features
        feature_size = self.selected_feature["size"] if "size" in [field.name() for field in self.layer.fields()] else 30.0
        
        # Konvertiere Map Units zu Pixeln
        map_units_per_pixel = self.canvas.mapUnitsPerPixel()
        size_in_pixels = feature_size / map_units_per_pixel
        
        # Feature-Position in Pixeln - manuelle Berechnung
        feature_point = self.selected_feature.geometry().asPoint()
        extent = self.canvas.extent()
        canvas_width = self.canvas.width()
        canvas_height = self.canvas.height()
        
        # Berechne Pixel-Position relativ zum Canvas
        pixel_x = ((feature_point.x() - extent.xMinimum()) / extent.width()) * canvas_width
        pixel_y = canvas_height - ((feature_point.y() - extent.yMinimum()) / extent.height()) * canvas_height
        pixel_point = QPoint(int(pixel_x), int(pixel_y))
        
        # Berechne Bounds (Quadrat um das Feature)
        half_size = size_in_pixels / 2
        self.selection_bounds = QRect(
            int(pixel_point.x() - half_size),
            int(pixel_point.y() - half_size),
            int(size_in_pixels),
            int(size_in_pixels)
        )
        
    def _create_resize_handles(self):
        """Erstellt die Resize-Punkte an den Ecken der Auswahl"""
        if not self.selection_bounds:
            return
            
        self.resize_handles = []
        bounds = self.selection_bounds
        
        # 8 Resize-Punkte: 4 Ecken + 4 Seiten-Mittelpunkte
        handles = [
            # Ecken
            QPoint(bounds.left(), bounds.top()),      # Oben links
            QPoint(bounds.right(), bounds.top()),    # Oben rechts
            QPoint(bounds.right(), bounds.bottom()), # Unten rechts
            QPoint(bounds.left(), bounds.bottom()),  # Unten links
            # Seiten-Mittelpunkte
            QPoint(bounds.center().x(), bounds.top()),     # Oben Mitte
            QPoint(bounds.right(), bounds.center().y()),   # Rechts Mitte
            QPoint(bounds.center().x(), bounds.bottom()),  # Unten Mitte
            QPoint(bounds.left(), bounds.center().y()),    # Links Mitte
        ]
        
        self.resize_handles = handles
        
    def _get_handle_at_position(self, pos):
        """Prüft, ob die Mausposition über einem Resize-Punkt ist"""
        for i, handle in enumerate(self.resize_handles):
            distance = math.sqrt((pos.x() - handle.x())**2 + (pos.y() - handle.y())**2)
            if distance <= self.handle_size:
                return i
        return None
        
    def _get_cursor_for_handle(self, handle_index):
        """Gibt den passenden Cursor für den Resize-Punkt zurück"""
        if handle_index is None:
            return Qt.ArrowCursor
            
        # Ecken
        if handle_index in [0, 2]:  # Oben links, unten rechts
            return Qt.SizeFDiagCursor
        elif handle_index in [1, 3]:  # Oben rechts, unten links
            return Qt.SizeBDiagCursor
        # Seiten-Mittelpunkte
        elif handle_index in [4, 6]:  # Oben/unten Mitte
            return Qt.SizeVerCursor
        elif handle_index in [5, 7]:  # Links/rechts Mitte
            return Qt.SizeHorCursor
        else:
            return Qt.ArrowCursor
            
    def canvasMoveEvent(self, event):
        """Behandelt Mausbewegungen"""
        if not self.selected_feature:
            return
            
        # Aktualisiere Bounds basierend auf aktueller Zoom-Stufe
        self._calculate_selection_bounds()
        self._create_resize_handles()
        
        # Prüfe, ob Maus über einem Resize-Punkt ist
        handle_index = self._get_handle_at_position(event.pos())
        
        if self.is_resizing:
            # Resize-Modus: Aktualisiere Größe basierend auf Mausbewegung
            self._update_feature_size(event.pos())
        else:
            # Normale Mausbewegung: Cursor entsprechend setzen
            cursor = self._get_cursor_for_handle(handle_index)
            self.setCursor(cursor)
            
    def canvasPressEvent(self, event):
        """Behandelt Mausklicks"""
        if event.button() != Qt.LeftButton or not self.selected_feature:
            return
            
        # Prüfe, ob Klick auf einem Resize-Punkt
        handle_index = self._get_handle_at_position(event.pos())
        
        if handle_index is not None:
            # Starte Resize-Modus
            self.active_handle = handle_index
            self.is_resizing = True
            self.resize_start_pos = event.pos()
            self.resize_start_size = self.selected_feature["size"]
            self.setCursor(self._get_cursor_for_handle(handle_index))
        else:
            # Klick außerhalb der Resize-Punkte - Feature deselektieren
            self.set_selected_feature(None)
            
    def canvasReleaseEvent(self, event):
        """Behandelt Mausloslassen"""
        if event.button() == Qt.LeftButton and self.is_resizing:
            # Beende Resize-Modus
            self.is_resizing = False
            self.active_handle = None
            self.resize_start_pos = None
            self.setCursor(Qt.ArrowCursor)
            
    def _update_feature_size(self, current_pos):
        """Aktualisiert die Größe des Features basierend auf der Mausbewegung"""
        if not self.is_resizing or not self.selected_feature or not self.resize_start_pos:
            return
            
        # Berechne die Größenänderung basierend auf dem aktiven Handle
        delta_x = current_pos.x() - self.resize_start_pos.x()
        delta_y = current_pos.y() - self.resize_start_pos.y()
        
        # Konvertiere Pixel zu Map Units
        map_units_per_pixel = self.canvas.mapUnitsPerPixel()
        
        # Berechne neue Größe basierend auf dem Handle-Typ
        if self.active_handle in [0, 2]:  # Diagonale Ecken
            delta_size = (delta_x + delta_y) * map_units_per_pixel
        elif self.active_handle in [1, 3]:  # Andere diagonale Ecken
            delta_size = (-delta_x + delta_y) * map_units_per_pixel
        elif self.active_handle in [4, 6]:  # Vertikale Seiten
            delta_size = delta_y * map_units_per_pixel
        elif self.active_handle in [5, 7]:  # Horizontale Seiten
            delta_size = delta_x * map_units_per_pixel
        else:
            delta_size = 0
            
        # Neue Größe berechnen
        new_size = max(5.0, min(500.0, self.resize_start_size + delta_size))
        
        # Feature-Größe aktualisieren
        if hasattr(self.layer_manager, 'resize_feature'):
            self.layer_manager.resize_feature(self.selected_feature.id(), new_size)
            
        # Bounds neu berechnen
        self._calculate_selection_bounds()
        self._create_resize_handles()
        
    def deactivate(self):
        """Deaktiviert das Tool und entfernt die Auswahl"""
        self.update_timer.stop()
        self.set_selected_feature(None)
        super().deactivate()