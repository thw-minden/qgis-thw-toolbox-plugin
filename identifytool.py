# identifytool.py

import os
import time
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QPushButton, QInputDialog, 
                           QLabel, QHBoxLayout, QDockWidget, QWidget, QSlider,
                           QCheckBox, QSpinBox, QApplication, QLineEdit)
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
        
        # SVG-Anzeige (Preview des ausgewählten Markers)
        self.svg_label = QLabel()
        self.svg_label.setAlignment(Qt.AlignCenter)
        self.svg_label.setMinimumHeight(200)
        self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; }")
        self.main_layout.addWidget(self.svg_label)
        
        # Platzhalter-Text für leeren Zustand
        self.placeholder_label = QLabel()
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setStyleSheet("QLabel { color: #666; font-size: 12px; padding: 20px; }")
        self.main_layout.addWidget(self.placeholder_label)
        
        # UTM 32N Koordinaten mit Kopier-Button
        coord_layout = QHBoxLayout()
        
        self.utm32n_label = QLabel("")
        self.utm32n_label.setAlignment(Qt.AlignCenter)
        self.utm32n_label.setStyleSheet("QLabel { color: #2E86AB; font-size: 12px; font-weight: bold; }")
        coord_layout.addWidget(self.utm32n_label)
        
        self.btn_copy_coords = QPushButton("Kopieren")
        self.btn_copy_coords.setStyleSheet("QPushButton { background-color: #2E86AB; color: white; border: none; padding: 2px 6px; border-radius: 3px; font-size: 10px; } QPushButton:hover { background-color: #1B5A7A; }")
        self.btn_copy_coords.setMaximumWidth(60)
        coord_layout.addWidget(self.btn_copy_coords)
        
        self.main_layout.addLayout(coord_layout)
        
        # Größen-SpinBox und Schieberegler
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Größe:"))
        
        self.size_spinbox = QSpinBox()
        self.size_spinbox.setMinimum(10)  # Minimale Größe
        self.size_spinbox.setMaximum(200)  # Maximale Größe
        self.size_spinbox.setValue(50)  # Standardwert
        self.size_spinbox.setSingleStep(1)  # Schrittweite
        size_layout.addWidget(self.size_spinbox)
        
        self.main_layout.addLayout(size_layout)
        
        # Schieberegler für Größe
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("Größe:"))
        
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(10)  # Minimale Größe
        self.size_slider.setMaximum(200)  # Maximale Größe
        self.size_slider.setValue(50)  # Standardwert
        self.size_slider.setTickPosition(QSlider.TicksBelow)
        self.size_slider.setTickInterval(20)  # Alle 20 Einheiten eine Markierung
        slider_layout.addWidget(self.size_slider)
        
        self.main_layout.addLayout(slider_layout)
        
        # Skalierungs-Checkbox
        self.scale_checkbox = QCheckBox("Mit Karte skalieren")
        self.main_layout.addWidget(self.scale_checkbox)
        
        # Label-Sektion
        label_layout = QVBoxLayout()
        label_layout.addWidget(QLabel("Label:"))
        
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("Label-Text eingeben...")
        self.label_input.setStyleSheet("QLineEdit { padding: 4px; border: 1px solid #ccc; border-radius: 3px; }")
        label_layout.addWidget(self.label_input)
        
        self.main_layout.addLayout(label_layout)
        
        # Label-Anzeige-Checkbox
        self.show_label_checkbox = QCheckBox("Label anzeigen")
        self.main_layout.addWidget(self.show_label_checkbox)
        
        # Eindeutige ID anzeigen
        self.id_label = QLabel("")
        self.id_label.setAlignment(Qt.AlignCenter)
        self.id_label.setStyleSheet("QLabel { color: #666; font-size: 10px; font-family: monospace; }")
        self.main_layout.addWidget(self.id_label)
        
        # Buttons in horizontalem Layout
        self.button_layout = QHBoxLayout()
        
        self.btn_delete = QPushButton("Löschen")
        self.button_layout.addWidget(self.btn_delete)
        
        self.main_layout.addLayout(self.button_layout)
        
        # Tracker für letzte Preview-Datei
        self.last_preview_path = None
        
        # Initial verstecken und Platzhalter anzeigen
        self.hide()
        self.show_placeholder()
        
    def show_placeholder(self):
        """Zeigt Platzhalter-Text mit Anweisungen an"""
        # Lösche letzte Preview-Datei, falls vorhanden
        try:
            if getattr(self, 'last_preview_path', None) and os.path.exists(self.last_preview_path):
                os.remove(self.last_preview_path)
                self.last_preview_path = None
        except Exception:
            pass
        
        self.svg_label.clear()
        self.svg_label.setText("Kein Marker ausgewählt")
        self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; color: #999; font-size: 14px; }")
        
        placeholder_text = """<b>Marker auswählen</b><br><br>
        • Klicken Sie auf einen Marker auf der Karte<br>
        • Oder ziehen Sie ein Symbol aus der Symbolpalette auf die Karte<br><br>
        Hier werden dann die Details des ausgewählten Markers angezeigt."""
        
        self.placeholder_label.setText(placeholder_text)
        self.placeholder_label.show()
        
        # Koordinaten und Steuerelemente verstecken
        self.utm32n_label.hide()
        self.btn_copy_coords.hide()
        self.size_spinbox.hide()
        self.size_slider.hide()
        self.scale_checkbox.hide()
        self.label_input.hide()
        self.show_label_checkbox.hide()
        self.id_label.hide()
        self.btn_delete.hide()
        
        # Dock-Titel ohne Koordinaten
        self.setWindowTitle("Marker Details")
        
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
    
        
    def show_feature(self, feat, layer_manager):
        self.feat = feat
        self.layer_manager = layer_manager
        
        # Debug: Zeige Feature-Daten
        print(f"DEBUG: Feature-Daten:")
        print(f"  - ID: {feat.id()}")
        print(f"  - SVG-Pfad: {feat.attribute('svg_path') if feat.attribute('svg_path') else 'N/A'}")
        print(f"  - SVG-Inhalt vorhanden: {bool(feat.attribute('svg_content'))}")
        print(f"  - SVG-Inhalt Länge: {len(feat.attribute('svg_content')) if feat.attribute('svg_content') else 0}")
        print(f"  - Größe: {feat.attribute('size') if feat.attribute('size') else 'N/A'}")
        
        # Platzhalter verstecken
        self.placeholder_label.hide()
        
        # SVG-Preview aktualisieren
        try:
            pixmap = None
            svg_path_feat = feat.attribute('svg_path')
            svg_content_feat = feat.attribute('svg_content') or ''
            
            # Versuche zuerst den SVG-Inhalt zu verwenden
            if svg_content_feat and svg_content_feat.strip():
                print("DEBUG: Versuche SVG-Inhalt zu verwenden")
                # Erstelle temporäre SVG-Datei für Preview
                temp_svg = self._create_temp_svg_for_preview(svg_content_feat)
                if temp_svg and os.path.exists(temp_svg):
                    print(f"DEBUG: Temporäre SVG-Datei erstellt: {temp_svg}")
                    # Versuche zuerst mit QIcon (bessere SVG-Unterstützung)
                    from PyQt5.QtGui import QIcon
                    icon = QIcon(temp_svg)
                    pixmap = icon.pixmap(180, 180)
                    if pixmap.isNull():
                        print("DEBUG: QIcon-Pixmap ist null, versuche QPixmap")
                        # Fallback auf QPixmap
                        pixmap = QPixmap(temp_svg)
                    else:
                        print("DEBUG: QIcon-Pixmap erfolgreich geladen")
                else:
                    print("DEBUG: Temporäre SVG-Datei konnte nicht erstellt werden")
            
            # Falls SVG-Inhalt nicht funktioniert hat, versuche den Pfad
            if pixmap is None or pixmap.isNull():
                print("DEBUG: Versuche SVG-Pfad zu verwenden")
                # Konvertiere relativen Pfad zu absolutem Pfad (wie im Haupt-Plugin)
                if not os.path.isabs(svg_path_feat):
                    plugin_dir = os.path.dirname(__file__)
                    absolute_path = os.path.join(plugin_dir, svg_path_feat)
                    print(f"DEBUG: Konvertierter absoluter Pfad: {absolute_path}")
                    if os.path.exists(absolute_path):
                        print("DEBUG: Absoluter Pfad existiert, lade SVG")
                        # Versuche zuerst mit QIcon (bessere SVG-Unterstützung)
                        from PyQt5.QtGui import QIcon
                        icon = QIcon(absolute_path)
                        pixmap = icon.pixmap(180, 180)
                        if pixmap.isNull():
                            print("DEBUG: QIcon-Pixmap ist null, versuche QPixmap")
                            # Fallback auf QPixmap
                            pixmap = QPixmap(absolute_path)
                        else:
                            print("DEBUG: QIcon-Pixmap erfolgreich geladen")
                    else:
                        print("DEBUG: Absoluter Pfad existiert nicht, verwende ursprünglichen Pfad")
                        # Fallback: Verwende den ursprünglichen Pfad
                        pixmap = QPixmap(svg_path_feat)
                else:
                    print("DEBUG: Pfad ist bereits absolut")
                    # Versuche zuerst mit QIcon (bessere SVG-Unterstützung)
                    from PyQt5.QtGui import QIcon
                    icon = QIcon(svg_path_feat)
                    pixmap = icon.pixmap(180, 180)
                    if pixmap.isNull():
                        print("DEBUG: QIcon-Pixmap ist null, versuche QPixmap")
                        # Fallback auf QPixmap
                        pixmap = QPixmap(svg_path_feat)
                    else:
                        print("DEBUG: QIcon-Pixmap erfolgreich geladen")
            
            # Prüfe ob das Pixmap erfolgreich geladen wurde
            if pixmap is not None and not pixmap.isNull():
                print("DEBUG: Pixmap erfolgreich geladen, skaliere für Vorschau")
                # Skaliere das Bild für die Vorschau
                scaled_pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.svg_label.setPixmap(scaled_pixmap)
                self.svg_label.setStyleSheet("QLabel { border: 2px solid #2E86AB; background-color: white; }")
                print("DEBUG: SVG-Preview erfolgreich angezeigt")
            else:
                print("DEBUG: Pixmap ist None oder null")
                raise Exception("Pixmap konnte nicht geladen werden")
            
        except Exception as e:
            print(f"Fehler beim Laden des SVG-Previews: {e}")
            print(f"SVG-Pfad: {feat.attribute('svg_path') if feat.attribute('svg_path') else 'N/A'}")
            print(f"SVG-Inhalt vorhanden: {bool(feat.attribute('svg_content'))}")
            print(f"SVG-Inhalt Länge: {len(feat.attribute('svg_content')) if feat.attribute('svg_content') else 0}")
            
            # Versuche alternative SVG-Loading-Methoden
            try:
                # Versuche mit QIcon (funktioniert oft besser mit SVG)
                from PyQt5.QtGui import QIcon
                svg_path_feat = feat.attribute('svg_path')
                if not os.path.isabs(svg_path_feat):
                    plugin_dir = os.path.dirname(__file__)
                    absolute_path = os.path.join(plugin_dir, svg_path_feat)
                    if os.path.exists(absolute_path):
                        icon = QIcon(absolute_path)
                        pixmap = icon.pixmap(180, 180)
                        if not pixmap.isNull():
                            self.svg_label.setPixmap(pixmap)
                            self.svg_label.setStyleSheet("QLabel { border: 2px solid #2E86AB; background-color: white; }")
                            return
            except Exception as e2:
                print(f"Alternative SVG-Loading-Methode fehlgeschlagen: {e2}")
            
            self.svg_label.setText("SVG konnte nicht geladen werden")
            self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; color: #999; font-size: 12px; }")
        
        # Koordinaten anzeigen
        if feat.geometry():
            point = feat.geometry().asPoint()
            source_crs = layer_manager.layer.crs()
            
            # Nur UTM 32N Koordinaten berechnen und anzeigen
            utm32n_text = self.convert_to_utm32n(point, source_crs)
            
            # Label aktualisieren
            self.utm32n_label.setText(utm32n_text)
            self.utm32n_label.show()
            
            # UTM-Koordinaten für Kopier-Funktion speichern
            self.current_utm_coords = utm32n_text
            
            # Dock-Titel ohne Koordinaten (nur "Marker Details")
            self.setWindowTitle("Marker Details")
        
        # Alle Steuerelemente anzeigen
        self.btn_copy_coords.show()
        self.size_spinbox.show()
        self.size_slider.show()
        self.scale_checkbox.show()
        self.label_input.show()
        self.show_label_checkbox.show()
        self.id_label.show()
        self.btn_delete.show()
        
        # SpinBox und Schieberegler auf aktuelle Größe setzen
        current_size = feat.attribute("size")
        self.size_spinbox.setValue(int(current_size))
        self.size_slider.setValue(int(current_size))
        
        # Checkbox auf aktuellen Wert setzen oder Standardwert verwenden
        try:
            scale_with_map = feat.attribute("scale_with_map")
        except:
            scale_with_map = False
        self.scale_checkbox.setChecked(scale_with_map)
        
        # Label-Werte setzen
        try:
            label_text = feat.attribute("label") or ""
            show_label = feat.attribute("show_label") or False
        except:
            label_text = ""
            show_label = False
        self.label_input.setText(label_text)
        self.show_label_checkbox.setChecked(show_label)
        
        # Eindeutige ID anzeigen
        try:
            unique_id = feat.attribute("unique_id") or ""
            if unique_id:
                # Zeige nur die ersten 8 Zeichen der UUID für bessere Lesbarkeit
                short_id = unique_id[:8] + "..."
                self.id_label.setText(f"ID: {short_id}")
            else:
                self.id_label.setText("ID: Nicht verfügbar")
        except:
            self.id_label.setText("ID: Nicht verfügbar")
        
        # Buttons, SpinBox, Schieberegler und Checkbox neu verbinden
        self.btn_delete.clicked.disconnect() if self.btn_delete.receivers(self.btn_delete.clicked) > 0 else None
        self.btn_copy_coords.clicked.disconnect() if self.btn_copy_coords.receivers(self.btn_copy_coords.clicked) > 0 else None
        self.size_spinbox.valueChanged.disconnect() if self.size_spinbox.receivers(self.size_spinbox.valueChanged) > 0 else None
        self.size_slider.valueChanged.disconnect() if self.size_slider.receivers(self.size_slider.valueChanged) > 0 else None
        self.scale_checkbox.stateChanged.disconnect() if self.scale_checkbox.receivers(self.scale_checkbox.stateChanged) > 0 else None
        self.label_input.textChanged.disconnect() if self.label_input.receivers(self.label_input.textChanged) > 0 else None
        self.show_label_checkbox.stateChanged.disconnect() if self.show_label_checkbox.receivers(self.show_label_checkbox.stateChanged) > 0 else None
        
        self.btn_delete.clicked.connect(self.on_delete)
        self.btn_copy_coords.clicked.connect(self.on_copy_coords)
        self.size_spinbox.valueChanged.connect(self.on_size_change)
        self.size_slider.valueChanged.connect(self.on_size_change)
        self.scale_checkbox.stateChanged.connect(self.on_scale_toggle)
        self.label_input.textChanged.connect(self.on_label_changed)
        self.show_label_checkbox.stateChanged.connect(self.on_show_label_toggle)
        
        # Synchronisation zwischen SpinBox und Schieberegler
        self.size_spinbox.valueChanged.connect(self.on_spinbox_changed)
        self.size_slider.valueChanged.connect(self.on_slider_changed)
        
        self.show()
        
    def _create_temp_svg_for_preview(self, svg_content):
        """Erstellt eine temporäre SVG-Datei für die Vorschau"""
        try:
            # Lösche vorherige Preview-Datei, falls vorhanden
            try:
                if getattr(self, 'last_preview_path', None) and os.path.exists(self.last_preview_path):
                    os.remove(self.last_preview_path)
                    self.last_preview_path = None
            except Exception:
                pass
            
            # Erstelle temporäres Verzeichnis falls es nicht existiert
            temp_dir = os.path.join(os.path.dirname(__file__), "temp_files", "preview_cache")
            os.makedirs(temp_dir, exist_ok=True)
            
            # Erstelle eindeutigen Dateinamen für Preview
            temp_filename = f"preview_{int(time.time() * 1000)}.svg"
            temp_path = os.path.join(temp_dir, temp_filename)
            
            # Schreibe SVG-Inhalt in temporäre Datei
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(svg_content)
            
            # Prüfe ob die Datei erfolgreich erstellt wurde
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                # Merke die zuletzt erzeugte Preview-Datei
                self.last_preview_path = temp_path
                return temp_path
            else:
                return None
                
        except Exception:
            return None
        
    def on_delete(self):
        self.layer_manager.delete_feature(self.feat.id())
        self.show_placeholder()
        self.show()
        
    def on_copy_coords(self):
        """Kopiert die UTM 32N Koordinaten in die Zwischenablage"""
        if hasattr(self, 'current_utm_coords'):
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_utm_coords)
            # Kurze visuelle Bestätigung
            self.btn_copy_coords.setText("Kopiert!")
            self.btn_copy_coords.setStyleSheet("QPushButton { background-color: #28a745; color: white; border: none; padding: 2px 6px; border-radius: 3px; font-size: 10px; }")
            
            # Nach 1 Sekunde zurücksetzen
            QTimer.singleShot(1000, self.reset_copy_button)
    
    def reset_copy_button(self):
        """Setzt den Kopier-Button zurück"""
        self.btn_copy_coords.setText("Kopieren")
        self.btn_copy_coords.setStyleSheet("QPushButton { background-color: #2E86AB; color: white; border: none; padding: 2px 6px; border-radius: 3px; font-size: 10px; } QPushButton:hover { background-color: #1B5A7A; }")
        
    def on_size_change(self, value):
        self.layer_manager.resize_feature(self.feat.id(), value)
        
    def on_scale_toggle(self, state):
        self.layer_manager.toggle_scale(self.feat.id(), state == Qt.Checked)
        
    def on_spinbox_changed(self, value):
        """Synchronisiert den Schieberegler mit der SpinBox"""
        self.size_slider.blockSignals(True)  # Verhindere Endlosschleife
        self.size_slider.setValue(value)
        self.size_slider.blockSignals(False)
        
    def on_slider_changed(self, value):
        """Synchronisiert die SpinBox mit dem Schieberegler"""
        self.size_spinbox.blockSignals(True)  # Verhindere Endlosschleife
        self.size_spinbox.setValue(value)
        self.size_spinbox.blockSignals(False)
        
    def on_label_changed(self, text):
        """Wird aufgerufen, wenn der Label-Text geändert wird"""
        if not hasattr(self, 'feat') or not self.feat:
            return
            
        # Label im Feature aktualisieren
        self.layer_manager.update_feature_label(self.feat.id(), text)
        
        # Feature-Daten aktualisieren
        updated_feat = self.layer_manager.layer.getFeature(self.feat.id())
        if updated_feat.isValid():
            self.feat = updated_feat
                
    def on_show_label_toggle(self, state):
        """Schaltet die Label-Anzeige ein/aus"""
        if not hasattr(self, 'feat') or not self.feat:
            return
            
        show_label = state == Qt.Checked
        self.layer_manager.toggle_label_visibility(self.feat.id(), show_label)
        
    def hideEvent(self, event):
        # Verschieben-Modus deaktivieren wenn Dock geschlossen wird
        if hasattr(self, 'layer_manager') and hasattr(self.layer_manager, 'move_tool'):
            self.layer_manager.move_tool.set_move_mode(False)
            # Cursor zurücksetzen
            if hasattr(self.layer_manager, 'canvas'):
                self.layer_manager.canvas.setCursor(Qt.ArrowCursor)
        
        # Zeige Platzhalter wenn Dock versteckt wird
        self.show_placeholder()
        
        # Versuche verbleibende Preview-Datei zu löschen
        try:
            if getattr(self, 'last_preview_path', None) and os.path.exists(self.last_preview_path):
                os.remove(self.last_preview_path)
                self.last_preview_path = None
        except Exception:
            pass
        
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
        # Zeige Dock mit Platzhalter initial
        self.feature_dock.show()
        self.feature_dock.show_placeholder()

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
            self.feature_dock.show_placeholder()
            self.feature_dock.show()
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