import os
import time

from qgis.core import QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapToolIdentify, QgsScaleWidget
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..layer.background import background_of
from ..layer.labeling import label_size_pt
from ..layer.renderer import scale_limits
from ..logging_utils import get_logger
from ..paths import plugin_root
from ..util.coordinates import WGS84, to_mgrs
from .background_popup import BackgroundButton
from .font_size_box import FontSizeBox
from .origin_point_widget import OriginPointWidget
from .theme import muted_color, muted_style

logger = get_logger(__name__)


def _load_svg_pixmap(path: str, size: int = 180) -> QPixmap | None:
    """Load `path` as a pixmap. Tries QIcon first (better SVG handling),
    then QPixmap as fallback. Returns None if both fail."""
    icon = QIcon(path)
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        pixmap = QPixmap(path)
    return pixmap if not pixmap.isNull() else None


def _scale_or_none(widget: QgsScaleWidget) -> float | None:
    """Maßstabszahl des Felds; leer (bzw. ungültig) = keine Grenze."""
    scale = widget.scale()
    return None if widget.isNull() or not scale > 0 else scale


class FeatureDock(QDockWidget):
    """Details zur Auswahl – für taktische Zeichen und Objektplanung gemeinsam.

    Drei Seiten: Hinweis (nichts ausgewählt), Einstellungen eines einzelnen
    taktischen Zeichens und die Objektseite der Objektplanung (``set_objects_page``)
    für Objekte und Mehrfachauswahl.
    """

    def __init__(self, parent=None):
        super().__init__("Details", parent)
        self.setObjectName("THWToolboxDetails")
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.feat = None
        self.objects_page = None

        self.stack = QStackedWidget()
        self.setWidget(self.stack)
        self._empty_page = self._build_empty_page()
        self.stack.addWidget(self._empty_page)

        # Seite „Zeichen“: Einstellungen eines einzelnen taktischen Zeichens
        self.content_widget = QWidget()
        self.main_layout = QVBoxLayout(self.content_widget)
        self._marker_page = QScrollArea()
        self._marker_page.setWidgetResizable(True)
        self._marker_page.setFrameShape(QFrame.Shape.NoFrame)
        self._marker_page.setWidget(self.content_widget)
        self.stack.addWidget(self._marker_page)

        # SVG-Anzeige (Preview des ausgewählten Markers)
        self.svg_label = QLabel()
        self.svg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.svg_label.setMinimumHeight(200)
        self.main_layout.addWidget(self.svg_label)

        # Position des Ankerpunkts (UTMREF + Breite/Länge) mit Kopier-Button
        self.position_widget = QWidget()
        position_layout = QVBoxLayout(self.position_widget)
        position_layout.setContentsMargins(0, 0, 0, 0)

        coord_layout = QHBoxLayout()
        self.utmref_label = QLabel("")
        coord_layout.addWidget(self.utmref_label)
        self.btn_copy_coords = QPushButton("Kopieren")
        self.btn_copy_coords.setMaximumWidth(60)
        coord_layout.addWidget(self.btn_copy_coords)
        position_layout.addLayout(coord_layout)

        self.latlon_label = QLabel("")
        position_layout.addWidget(self.latlon_label)

        # Koordinaten sind Links: Klick öffnet die Positionseingabe
        for label in (self.utmref_label, self.latlon_label):
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setToolTip("Klicken, um die Position per Koordinate zu setzen")
            label.linkActivated.connect(self.on_edit_position)

        self.main_layout.addWidget(self.position_widget)

        # Label und Darstellung
        label_layout = QHBoxLayout()
        self.label_textfield_label = QLabel("Label:")
        label_layout.addWidget(self.label_textfield_label)
        self.label_textfield = QLineEdit()
        label_layout.addWidget(self.label_textfield)
        self.cb_enable_label = QCheckBox("Label auf Karte zeigen")
        label_layout.addWidget(self.cb_enable_label)
        self.main_layout.addLayout(label_layout)

        font_size_layout = QHBoxLayout()
        font_size_layout.addWidget(QLabel("Schriftgröße:"))
        self.font_size_box = FontSizeBox()
        self.font_size_box.size_chosen.connect(self.on_font_size_chosen)
        font_size_layout.addWidget(self.font_size_box)
        font_size_layout.addWidget(QLabel("pt"))
        font_size_layout.addStretch()
        self.main_layout.addLayout(font_size_layout)

        # Größen-SpinBox und Schieberegler
        size_layout = QHBoxLayout()
        self.size_label = QLabel("Größe:")
        size_layout.addWidget(self.size_label)

        self.size_spinbox = QSpinBox()
        # Klein genug für Millimeter-Größen nach dem Umschalten von „Mit Karte skalieren“
        self.size_spinbox.setMinimum(1)
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
        self.size_slider.setMinimum(1)  # Minimale Größe
        self.size_slider.setMaximum(200)  # Maximale Größe
        self.size_slider.setValue(50)  # Standardwert
        self.size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.size_slider.setTickInterval(20)  # Alle 20 Einheiten eine Markierung
        slider_layout.addWidget(self.size_slider)

        self.main_layout.addLayout(slider_layout)

        # Skalierungs-Checkbox
        self.scale_checkbox = QCheckBox("Mit Karte skalieren")
        self.main_layout.addWidget(self.scale_checkbox)

        # Grenzen fürs Mitskalieren: darüber hinaus behält das Zeichen seine Größe
        limits_layout = QFormLayout()
        limits_layout.setContentsMargins(20, 0, 0, 0)
        self.max_scale_widget = self._scale_limit_widget(
            "Beim Reinzoomen über diesen Maßstab hinaus wird das Zeichen nicht mehr größer.\n"
            "Leer = wächst immer mit. Der Knopf daneben übernimmt den aktuellen Maßstab."
        )
        limits_layout.addRow("Wächst bis:", self.max_scale_widget)
        self.min_scale_widget = self._scale_limit_widget(
            "Beim Rauszoomen über diesen Maßstab hinaus wird das Zeichen nicht mehr kleiner.\n"
            "Leer = schrumpft immer mit. Der Knopf daneben übernimmt den aktuellen Maßstab."
        )
        limits_layout.addRow("Schrumpft bis:", self.min_scale_widget)
        self.scale_limits_widget = QWidget()
        self.scale_limits_widget.setLayout(limits_layout)
        self.main_layout.addWidget(self.scale_limits_widget)

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

        # Hintergrund: Vorschau-Schaltfläche, Form/Farbe/Rand/Größe im Popup
        background_layout = QHBoxLayout()
        background_layout.addWidget(QLabel("Hintergrund:"))
        self.background_button = BackgroundButton()
        self.background_button.changed.connect(self.on_background_changed)
        background_layout.addWidget(self.background_button)
        background_layout.addStretch()
        self.main_layout.addLayout(background_layout)

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

        # Erst sichtbar, wenn Toolbox oder Objektplanung offen sind
        self.hide()
        self.show_placeholder()

    def _scale_limit_widget(self, tooltip: str) -> QgsScaleWidget:
        widget = QgsScaleWidget()
        widget.setAllowNull(True)
        widget.setNull()
        widget.setShowCurrentScaleButton(True)
        widget.setToolTip(tooltip)
        widget.scaleChanged.connect(self.on_scale_limits_changed)
        return widget

    @staticmethod
    def _build_empty_page() -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Nichts ausgewählt")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        hint = QLabel(
            "• Klicken Sie auf ein taktisches Zeichen oder ein Objekt der Objektplanung<br>"
            "• Shift+Klick oder Rahmen aufziehen wählt mehrere aus<br>"
            "• Oder ziehen Sie ein Symbol aus der Symbolpalette auf die Karte"
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style())
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def set_objects_page(self, page: QWidget):
        """Seite für Objekte der Objektplanung und Mehrfachauswahl (``planning.details.SelectionDetails``)."""
        self.objects_page = page
        self.stack.addWidget(page)

    def show_objects(self):
        if self.objects_page is None:
            return
        self._remove_preview_file()
        self.feat = None
        self.objects_page.refresh()
        self.stack.setCurrentWidget(self.objects_page)

    def showing_marker(self, fid=None) -> bool:
        """Zeigt das Dock gerade die Einstellungen eines Zeichens (``fid``: genau dieses)?"""
        if self.feat is None or self.stack.currentWidget() is not self._marker_page:
            return False
        return fid is None or self.feat.id() == fid

    def _remove_preview_file(self):
        try:
            if getattr(self, "last_preview_path", None) and os.path.exists(self.last_preview_path):
                os.remove(self.last_preview_path)
                self.last_preview_path = None
        except Exception as e:
            logger.debug("Konnte Preview-Datei %s nicht löschen: %s", getattr(self, "last_preview_path", None), e)

    def show_placeholder(self):
        """Nichts ausgewählt: Hinweise anzeigen."""
        self._remove_preview_file()
        self.feat = None
        self.svg_label.clear()
        self.stack.setCurrentWidget(self._empty_page)

    def _show_position(self, point, source_crs):
        """Zeigt die aktuelle Marker-Position als UTMREF und Breite/Länge an."""
        try:
            wgs = QgsCoordinateTransform(source_crs, WGS84, QgsProject.instance()).transform(point)
        except Exception:
            logger.exception("Konnte Marker-Position nicht nach WGS84 umrechnen")
            self.latlon_label.clear()
            self.utmref_label.setText("UTMREF: Fehler")
            self.current_utm_coords = ""
            return

        self.latlon_label.setText(f'Breite/Länge: <a href="edit">{wgs.y():.6f} {wgs.x():.6f}</a>')
        try:
            utmref = to_mgrs(wgs.y(), wgs.x())
        except ValueError:
            utmref = ""
        self.utmref_label.setText(f'UTMREF: <a href="edit">{utmref or "–"}</a>')
        self.current_utm_coords = utmref

    def on_edit_position(self, _link=None):
        """Klick auf die Koordinaten: Popup für „Breite Länge“ oder UTMREF öffnen."""
        if not getattr(self, "feat", None):
            return
        self.layer_manager.open_position_dialog(self.feat.id())

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

        self.stack.setCurrentWidget(self._marker_page)

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
            muted = muted_color()
            self.svg_label.setStyleSheet(f"QLabel {{ border: 2px dashed {muted}; color: {muted}; }}")

        # Koordinaten anzeigen
        if feat.geometry():
            self._show_position(feat.geometry().asPoint(), layer_manager.layer.crs())

        # SpinBox und Schieberegler auf aktuelle Größe setzen
        self._set_size_widgets(feat.attribute("size"))

        # Checkbox auf aktuellen Wert setzen oder Standardwert verwenden.
        # Das gespeicherte Attribut ist invertiert (True = feste Bildschirmgröße),
        # siehe layer.renderer.size_unit. Signale blockieren: Anzeigen ist kein Umschalten.
        try:
            scale_with_map = bool(feat.attribute("scale_with_map"))
        except Exception:
            scale_with_map = False
        self.scale_checkbox.blockSignals(True)
        self.scale_checkbox.setChecked(not scale_with_map)
        self.scale_checkbox.blockSignals(False)
        self._show_scale_limits(feat, layer_manager.canvas)

        # Label-Werte setzen
        try:
            label_text = feat.attribute("label") or ""
            show_label = feat.attribute("show_label") or False
        except Exception:
            label_text = ""
            show_label = False
        self.label_textfield.setText(label_text)
        self.cb_enable_label.setChecked(show_label)
        self.font_size_box.set_sizes([label_size_pt(feat, layer_manager.settings)])

        # Hintergrund anzeigen (ohne ihn zurückzuschreiben)
        self.background_button.set_background(background_of(feat))

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
        self.rotation_slider.valueChanged.connect(self.on_rotation_change)
        self.rotation_slider.sliderReleased.connect(self.on_rotation_slider_released)
        self.origin_widget.origin_changed.connect(self.on_origin_changed)

        # Synchronisation zwischen SpinBox und Schieberegler
        self.size_spinbox.valueChanged.connect(self.on_spinbox_changed)
        self.size_slider.valueChanged.connect(self.on_slider_changed)
        # Zeigen und nach vorn holen entscheidet das Plugin anhand der Auswahl
        # (sonst würde jedes Drag-Update alle 300 ms den Tab nach vorn holen)

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
        fid = self.feat.id()
        tool = getattr(self.layer_manager, "move_tool", None)
        if tool is not None and tool.selected_fid == fid:
            tool.clear_selection()
        self.layer_manager.delete_feature(fid)
        self.layer_manager.show_marker_placeholder()

    def on_copy_coords(self):
        """Kopiert die UTMREF-Koordinate in die Zwischenablage"""
        if getattr(self, "current_utm_coords", ""):
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
        new_size = self.layer_manager.toggle_scale(self.feat.id(), state == Qt.CheckState.Checked)
        # Größe wurde auf die neue Einheit (Meter ↔ Millimeter) umgerechnet
        if new_size is not None:
            self._set_size_widgets(new_size)
        self.scale_limits_widget.setVisible(self.scale_checkbox.isChecked())

    def _show_scale_limits(self, feat, canvas):
        """Grenzen fürs Mitskalieren anzeigen – nur bei „Mit Karte skalieren“ von Belang."""
        limits = scale_limits(feat)
        for widget, scale in ((self.max_scale_widget, limits.maxScale), (self.min_scale_widget, limits.minScale)):
            widget.setMapCanvas(canvas)
            widget.blockSignals(True)
            if scale > 0:
                widget.setScale(scale)
            else:
                widget.setNull()
            widget.blockSignals(False)
        self.scale_limits_widget.setVisible(self.scale_checkbox.isChecked())

    def on_scale_limits_changed(self, *_args):
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_scale_limits(
            self.feat.id(), _scale_or_none(self.max_scale_widget), _scale_or_none(self.min_scale_widget)
        )

    def on_font_size_chosen(self, size_pt: float):
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_label_size(self.feat.id(), size_pt)

    def _set_size_widgets(self, size):
        """SpinBox und Schieberegler auf `size` setzen, ohne das Feature zu ändern."""
        value = int(round(float(size or 0)))
        for widget in (self.size_spinbox, self.size_slider):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

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

    def on_background_changed(self, background):
        """Hintergrund aus dem Popup übernehmen."""
        if not getattr(self, "feat", None):
            return
        self.layer_manager.set_background(self.feat.id(), background)

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
        # Die Details schließen auch mit der Objektplanung – dann ohne MoveTool
        move_tool = getattr(getattr(self, "layer_manager", None), "move_tool", None)
        if move_tool is not None:
            move_tool.set_move_mode(False)
            # Cursor zurücksetzen
            if hasattr(self.layer_manager, "canvas"):
                self.layer_manager.canvas.setCursor(Qt.CursorShape.ArrowCursor)

        # Zeige Platzhalter wenn Dock versteckt wird (löscht auch die Preview-Datei)
        self.show_placeholder()

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
