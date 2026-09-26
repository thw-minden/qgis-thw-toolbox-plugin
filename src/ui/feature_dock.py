import os
import time

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapToolIdentify
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..logging_utils import get_logger
from ..paths import plugin_root
from .origin_point_widget import OriginPointWidget

logger = get_logger(__name__)


def _load_svg_pixmap(path: str, size: int = 180) -> QPixmap | None:
    """Load `path` as a pixmap. Tries QIcon first (better SVG handling),
    then QPixmap as fallback. Returns None if both fail."""
    icon = QIcon(path)
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        pixmap = QPixmap(path)
    return pixmap if not pixmap.isNull() else None


class FeatureDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Marker Details", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)

        # Hauptwidget für den Inhalt
        self.content_widget = QWidget()
        self.setWidget(self.content_widget)
        self.main_layout = QVBoxLayout(self.content_widget)

        # SVG-Anzeige (Preview des ausgewählten Markers)
        self.svg_label = QLabel()
        self.svg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.svg_label.setMinimumHeight(200)
        self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; }")
        self.main_layout.addWidget(self.svg_label)

        # Platzhalter-Text für leeren Zustand
        self.placeholder_label = QLabel()
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setStyleSheet("QLabel { color: #666; padding: 20px; }")
        self.main_layout.addWidget(self.placeholder_label)

        # UTM 32N Koordinaten mit Kopier-Button
        coord_layout = QHBoxLayout()

        self.utm32n_label = QLabel("")
        self.utm32n_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        coord_layout.addWidget(self.utm32n_label)

        self.btn_copy_coords = QPushButton("Kopieren")
        self.btn_copy_coords.setMaximumWidth(60)
        coord_layout.addWidget(self.btn_copy_coords)

        self.main_layout.addLayout(coord_layout)

        # Label und Darstellung
        label_layout = QHBoxLayout()
        self.label_textfield_label = QLabel("Label:")
        label_layout.addWidget(self.label_textfield_label)
        self.label_textfield = QLineEdit()
        label_layout.addWidget(self.label_textfield)
        self.cb_enable_label = QCheckBox("Label auf Karte zeigen")
        label_layout.addWidget(self.cb_enable_label)
        self.main_layout.addLayout(label_layout)

        # Größen-SpinBox und Schieberegler
        size_layout = QHBoxLayout()
        self.size_label = QLabel("Größe:")
        size_layout.addWidget(self.size_label)

        self.size_spinbox = QSpinBox()
        self.size_spinbox.setMinimum(10)
        self.size_spinbox.setMaximum(2000)
        self.size_spinbox.setValue(50)
        self.size_spinbox.setSingleStep(1)
        size_layout.addWidget(self.size_spinbox)

        self.main_layout.addLayout(size_layout)

        # Schieberegler für Größe
        slider_layout = QHBoxLayout()
        self.size_slider_label = QLabel("Größe:")
        slider_layout.addWidget(self.size_slider_label)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setMinimum(10)  # Minimale Größe
        self.size_slider.setMaximum(200)  # Maximale Größe
        self.size_slider.setValue(50)  # Standardwert
        self.size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.size_slider.setTickInterval(20)  # Alle 20 Einheiten eine Markierung
        slider_layout.addWidget(self.size_slider)

        self.main_layout.addLayout(slider_layout)

        # Skalierungs-Checkbox
        self.scale_checkbox = QCheckBox("Mit Karte skalieren")
        self.main_layout.addWidget(self.scale_checkbox)

        # Rotations-Schieberegler
        rotation_layout = QHBoxLayout()
        self.rotation_label = QLabel("Rotation:")
        rotation_layout.addWidget(self.rotation_label)

        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setMinimum(-180)
        self.rotation_slider.setMaximum(180)
        self.rotation_slider.setValue(0)
        self.rotation_slider.setSingleStep(10)
        self.rotation_slider.setPageStep(10)
        self.rotation_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.rotation_slider.setTickInterval(30)
        rotation_layout.addWidget(self.rotation_slider)

        self.rotation_value_label = QLabel("0°")
        self.rotation_value_label.setMinimumWidth(40)
        rotation_layout.addWidget(self.rotation_value_label)

        self.main_layout.addLayout(rotation_layout)

        # Ankerpunkt (Origin Point)
        origin_layout = QHBoxLayout()
        self.origin_label = QLabel("Ankerpunkt:")
        origin_layout.addWidget(self.origin_label)
        self.origin_widget = OriginPointWidget()
        origin_layout.addWidget(self.origin_widget)
        origin_layout.addStretch()
        self.main_layout.addLayout(origin_layout)

        # Weißer Hintergrund-Checkbox
        self.white_background_checkbox = QCheckBox("Weißer Hintergrund")
        self.main_layout.addWidget(self.white_background_checkbox)

        # Buttons in horizontalem Layout
        self.button_layout = QHBoxLayout()

        self.btn_delete = QPushButton("Löschen")
        self.button_layout.addWidget(self.btn_delete)

        self.main_layout.addLayout(self.button_layout)

        # Spacer am Ende hinzufügen, um alles nach oben auszurichten
        spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.main_layout.addItem(spacer)

        # Tracker für letzte Preview-Datei
        self.last_preview_path = None

        # Initial verstecken und Platzhalter anzeigen
        self.hide()
        self.show_placeholder()

    def show_placeholder(self):
        """Zeigt Platzhalter-Text mit Anweisungen an"""
        # Lösche letzte Preview-Datei, falls vorhanden
        try:
            if getattr(self, "last_preview_path", None) and os.path.exists(self.last_preview_path):
                os.remove(self.last_preview_path)
                self.last_preview_path = None
        except Exception as e:
            logger.debug("Konnte Preview-Datei %s nicht löschen: %s", getattr(self, "last_preview_path", None), e)

        self.svg_label.clear()
        self.svg_label.setText("Kein Marker ausgewählt")
        self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; color: #999; }")

        placeholder_text = """<b>Marker auswählen</b><br><br>
        • Klicken Sie auf einen Marker auf der Karte<br>
        • Oder ziehen Sie ein Symbol aus der Symbolpalette auf die Karte"""

        self.placeholder_label.setText(placeholder_text)
        self.placeholder_label.show()

        # Koordinaten und Steuerelemente verstecken
        self.utm32n_label.hide()
        self.btn_copy_coords.hide()
        self.size_label.hide()
        self.size_spinbox.hide()
        self.size_slider_label.hide()
        self.size_slider.hide()
        self.scale_checkbox.hide()
        self.rotation_label.hide()
        self.rotation_slider.hide()
        self.rotation_value_label.hide()
        self.origin_label.hide()
        self.origin_widget.hide()
        self.label_textfield_label.hide()
        self.label_textfield.hide()
        self.cb_enable_label.hide()
        self.white_background_checkbox.hide()
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
        except Exception:
            return "UTM 32N: Fehler"

    def show_feature(self, feat, layer_manager):
        self.feat = feat
        self.layer_manager = layer_manager

        logger.debug(
            "show_feature id=%s svg_path=%s svg_content_len=%d size=%s",
            feat.id(),
            feat.attribute("svg_path") or "N/A",
            len(feat.attribute("svg_content") or ""),
            feat.attribute("size") or "N/A",
        )

        self.placeholder_label.hide()

        try:
            pixmap = None
            svg_path_feat = feat.attribute("svg_path")
            svg_content_feat = feat.attribute("svg_content") or ""

            if svg_content_feat.strip():
                temp_svg = self._create_temp_svg_for_preview(svg_content_feat)
                if temp_svg and os.path.exists(temp_svg):
                    pixmap = _load_svg_pixmap(temp_svg)

            if pixmap is None or pixmap.isNull():
                if not os.path.isabs(svg_path_feat):
                    absolute_path = os.path.join(plugin_root(), svg_path_feat)
                    pixmap = _load_svg_pixmap(absolute_path if os.path.exists(absolute_path) else svg_path_feat)
                else:
                    pixmap = _load_svg_pixmap(svg_path_feat)

            if pixmap is not None and not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    180, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.svg_label.setPixmap(scaled_pixmap)
                self.svg_label.setStyleSheet("QLabel { border: 2px solid #2E86AB; background-color: white; }")
            else:
                raise Exception("Pixmap konnte nicht geladen werden")

        except Exception as e:
            logger.warning(
                "Fehler beim Laden des SVG-Previews: %s (svg_path=%s, svg_content_len=%d)",
                e,
                feat.attribute("svg_path") or "N/A",
                len(feat.attribute("svg_content") or ""),
            )
            self.svg_label.setText("SVG konnte nicht geladen werden")
            self.svg_label.setStyleSheet("QLabel { border: 2px dashed #ccc; background-color: #f9f9f9; color: #999; }")

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
        self.size_label.show()
        self.size_spinbox.show()
        self.size_slider_label.show()
        self.size_slider.show()
        self.scale_checkbox.show()
        self.rotation_label.show()
        self.rotation_slider.show()
        self.rotation_value_label.show()
        self.origin_label.show()
        self.origin_widget.show()
        # Label-Funktion vorerst ausgeblendet (Code bleibt für später erhalten)
        self.label_textfield_label.show()
        self.label_textfield.show()
        self.cb_enable_label.show()
        self.white_background_checkbox.show()
        self.btn_delete.show()

        # SpinBox und Schieberegler auf aktuelle Größe setzen
        current_size = int(feat.attribute("size"))
        self.size_spinbox.blockSignals(True)
        self.size_spinbox.setValue(current_size)
        self.size_spinbox.blockSignals(False)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(current_size)
        self.size_slider.blockSignals(False)

        # Checkbox auf aktuellen Wert setzen oder Standardwert verwenden
        try:
            scale_with_map = feat.attribute("scale_with_map")
        except Exception:
            scale_with_map = False
        self.scale_checkbox.setChecked(scale_with_map)

        # Label-Werte setzen
        try:
            label_text = feat.attribute("label") or ""
            show_label = feat.attribute("show_label") or False
        except Exception:
            label_text = ""
            show_label = False
        self.label_textfield.setText(label_text)
        self.cb_enable_label.setChecked(show_label)

        # Weißer Hintergrund-Wert setzen
        try:
            white_background = feat.attribute("white_background") or False
        except Exception:
            white_background = False
        self.white_background_checkbox.setChecked(white_background)

        # Rotationswert setzen
        try:
            rotation = feat.attribute("rotation") or 0.0
        except Exception:
            rotation = 0.0
        rotation = ((float(rotation) + 180.0) % 360.0) - 180.0
        # Signale blockieren: sonst würde on_rotation_change (noch verbunden)
        # frei gedrehte Werte auf 10°-Schritte runden und zurückschreiben.
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(rotation))
        self.rotation_slider.blockSignals(False)
        self.rotation_value_label.setText(f"{int(rotation)}°")

        # Ankerpunkt setzen
        try:
            origin_x = feat.attribute("origin_x")
            origin_y = feat.attribute("origin_y")
            if origin_x is None:
                origin_x = 1
            if origin_y is None:
                origin_y = 1
        except Exception:
            origin_x = 1
            origin_y = 1
        self.origin_widget.set_origin(int(origin_x), int(origin_y))

        # Buttons, SpinBox, Schieberegler und Checkbox neu verbinden
        self.btn_delete.clicked.disconnect() if self.btn_delete.receivers(self.btn_delete.clicked) > 0 else None
        self.btn_copy_coords.clicked.disconnect() if self.btn_copy_coords.receivers(
            self.btn_copy_coords.clicked
        ) > 0 else None
        self.size_spinbox.valueChanged.disconnect() if self.size_spinbox.receivers(
            self.size_spinbox.valueChanged
        ) > 0 else None
        self.size_slider.valueChanged.disconnect() if self.size_slider.receivers(
            self.size_slider.valueChanged
        ) > 0 else None
        self.scale_checkbox.stateChanged.disconnect() if self.scale_checkbox.receivers(
            self.scale_checkbox.stateChanged
        ) > 0 else None
        self.label_textfield.textChanged.disconnect() if self.label_textfield.receivers(
            self.label_textfield.textChanged
        ) > 0 else None
        self.cb_enable_label.stateChanged.disconnect() if self.cb_enable_label.receivers(
            self.cb_enable_label.stateChanged
        ) > 0 else None
        self.white_background_checkbox.stateChanged.disconnect() if self.white_background_checkbox.receivers(
            self.white_background_checkbox.stateChanged
        ) > 0 else None
        self.rotation_slider.valueChanged.disconnect() if self.rotation_slider.receivers(
            self.rotation_slider.valueChanged
        ) > 0 else None
        self.rotation_slider.sliderReleased.disconnect() if self.rotation_slider.receivers(
            self.rotation_slider.sliderReleased
        ) > 0 else None
        try:
            self.origin_widget.origin_changed.disconnect()
        except TypeError:
            pass

        self.btn_delete.clicked.connect(self.on_delete)
        self.btn_copy_coords.clicked.connect(self.on_copy_coords)
        self.size_spinbox.valueChanged.connect(self.on_size_change)
        self.size_slider.valueChanged.connect(self.on_size_change)
        self.scale_checkbox.stateChanged.connect(self.on_scale_toggle)
        self.label_textfield.textChanged.connect(self.on_label_changed)
        self.cb_enable_label.stateChanged.connect(self.on_show_label_toggle)
        self.white_background_checkbox.stateChanged.connect(self.on_white_background_toggle)
        self.rotation_slider.valueChanged.connect(self.on_rotation_change)
        self.rotation_slider.sliderReleased.connect(self.on_rotation_slider_released)
        self.origin_widget.origin_changed.connect(self.on_origin_changed)

        # Synchronisation zwischen SpinBox und Schieberegler
        self.size_spinbox.valueChanged.connect(self.on_spinbox_changed)
        self.size_slider.valueChanged.connect(self.on_slider_changed)

        self.show()

    def _create_temp_svg_for_preview(self, svg_content):
        """Erstellt eine temporäre SVG-Datei für die Vorschau"""
        try:
            # Lösche vorherige Preview-Datei, falls vorhanden
            try:
                if getattr(self, "last_preview_path", None) and os.path.exists(self.last_preview_path):
                    os.remove(self.last_preview_path)
                    self.last_preview_path = None
            except Exception as e:
                logger.debug(
                    "Konnte vorherige Preview-Datei %s nicht löschen: %s", getattr(self, "last_preview_path", None), e
                )

            # Erstelle temporäres Verzeichnis falls es nicht existiert
            temp_dir = os.path.join(plugin_root(), "temp_files", "preview_cache")
            os.makedirs(temp_dir, exist_ok=True)

            # Erstelle eindeutigen Dateinamen für Preview
            temp_filename = f"preview_{int(time.time() * 1000)}.svg"
            temp_path = os.path.join(temp_dir, temp_filename)

            # Schreibe SVG-Inhalt in temporäre Datei
            with open(temp_path, "w", encoding="utf-8") as f:
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
        if hasattr(self, "current_utm_coords"):
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_utm_coords)
            # Kurze visuelle Bestätigung
            self.btn_copy_coords.setText("Kopiert!")

            # Nach 1 Sekunde zurücksetzen
            QTimer.singleShot(1000, self.reset_copy_button)

    def reset_copy_button(self):
        """Setzt den Kopier-Button zurück"""
        self.btn_copy_coords.setText("Kopieren")

    def on_size_change(self, value):
        self.layer_manager.resize_feature(self.feat.id(), value)

    def on_scale_toggle(self, state):
        self.layer_manager.toggle_scale(self.feat.id(), state == Qt.CheckState.Checked)

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
        if not hasattr(self, "feat") or not self.feat:
            return

        # Label im Feature aktualisieren
        self.layer_manager.update_feature_label(self.feat.id(), text)

        # Feature-Daten aktualisieren
        updated_feat = self.layer_manager.layer.getFeature(self.feat.id())
        if updated_feat.isValid():
            self.feat = updated_feat

    def on_show_label_toggle(self, state):
        """Schaltet die Label-Anzeige ein/aus"""
        if not hasattr(self, "feat") or not self.feat:
            return

        show_label = state == Qt.CheckState.Checked
        self.layer_manager.toggle_label_visibility(self.feat.id(), show_label)

    def on_white_background_toggle(self, state):
        """Schaltet den weißen Hintergrund ein/aus"""
        if not hasattr(self, "feat") or not self.feat:
            return

        white_background = state == Qt.CheckState.Checked
        self.layer_manager.toggle_white_background(self.feat.id(), white_background)

    def on_rotation_change(self, value):
        """Wird aufgerufen, wenn der Rotationsschieberegler geändert wird"""
        if not hasattr(self, "feat") or not self.feat:
            return

        snapped = int(round(value / 10.0)) * 10
        if snapped != value:
            self.rotation_slider.blockSignals(True)
            self.rotation_slider.setValue(snapped)
            self.rotation_slider.blockSignals(False)

        self.rotation_value_label.setText(f"{snapped}°")
        self.layer_manager.rotate_feature(self.feat.id(), float(snapped))

    def on_rotation_slider_released(self):
        """Wird aufgerufen, wenn der Rotationsschieberegler losgelassen wird"""
        pass

    def on_origin_changed(self, origin_x, origin_y):
        """Wird aufgerufen, wenn der Ankerpunkt geaendert wird"""
        if not hasattr(self, "feat") or not self.feat:
            return

        self.layer_manager.update_origin(self.feat.id(), origin_x, origin_y)

    def hideEvent(self, event):
        # Verschieben-Modus deaktivieren wenn Dock geschlossen wird
        if hasattr(self, "layer_manager") and hasattr(self.layer_manager, "move_tool"):
            self.layer_manager.move_tool.set_move_mode(False)
            # Cursor zurücksetzen
            if hasattr(self.layer_manager, "canvas"):
                self.layer_manager.canvas.setCursor(Qt.CursorShape.ArrowCursor)

        # Zeige Platzhalter wenn Dock versteckt wird
        self.show_placeholder()

        # Versuche verbleibende Preview-Datei zu löschen
        try:
            if getattr(self, "last_preview_path", None) and os.path.exists(self.last_preview_path):
                os.remove(self.last_preview_path)
                self.last_preview_path = None
        except Exception as e:
            logger.debug(
                "Konnte Preview-Datei beim Verstecken nicht löschen %s: %s", getattr(self, "last_preview_path", None), e
            )

        super().hideEvent(event)


class IdentifyTool(QgsMapToolIdentify):
    def __init__(self, canvas, layer_manager):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer_manager = layer_manager
        self.layer = layer_manager.layer
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # Dock-Widget erstellen
        self.feature_dock = FeatureDock(canvas.parent())
        canvas.parent().addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.feature_dock)
        # Zeige Dock mit Platzhalter initial
        self.feature_dock.show()
        self.feature_dock.show_placeholder()

    def canvasReleaseEvent(self, event):
        # nur Linksklick
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Identifiziere erstes Feature unter Maus mit größerem Suchradius
        search_radius = 10  # Suchradius in Pixeln
        results = self.identify(
            event.x(), event.y(), [self.layer], QgsMapToolIdentify.IdentifyMode.TopDownStopAtFirst, search_radius
        )
        if not results:
            self.feature_dock.show_placeholder()
            self.feature_dock.show()
            if hasattr(self.layer_manager, "move_tool"):
                self.layer_manager.move_tool.set_move_mode(False)
            return

        feat = results[0].mFeature
        # Feature im Dock anzeigen
        self.feature_dock.show_feature(feat, self.layer_manager)

        # Automatisch in den Verschiebe-Modus wechseln
        if hasattr(self.layer_manager, "move_tool"):
            self.layer_manager.move_tool.set_move_mode(True)
            # Cursor auf ClosedHand ändern, um anzuzeigen, dass das Feature verschiebbar ist
            self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)
