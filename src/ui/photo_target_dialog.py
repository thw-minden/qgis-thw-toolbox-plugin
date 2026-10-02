"""Zielkoordinate aus einem Drohnenfoto bestimmen und dort ein Taktisches Zeichen setzen."""

import math
import os
import re

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSettings,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..logging_utils import get_logger
from ..util.coordinates import WGS84, to_mgrs
from ..util.drone_photo import (
    PhotoMetadata,
    azimuth,
    camera_ray,
    intersect_plane,
    intersect_terrain,
    read_photo_metadata,
)
from .symbol_picker import SymbolPickerDialog, symbol_catalog

logger = get_logger(__name__)

_LAST_DIR_KEY = "thw_toolbox/photo_target/last_dir"
_MAX_DISPLAY = 3200  # längere Bildkante für die Anzeige; gerechnet wird in Originalpixeln
_PHOTO_SUFFIXES = (".jpg", ".jpeg")
_DEM_NAME_RE = re.compile(r"dgm|dtm|dem|gel(ä|ae)nde|h(ö|oe)he|elevation|terrain", re.IGNORECASE)
_SHALLOW_RAY = math.sin(math.radians(3))  # flacher als 3° unter dem Horizont → Warnung

MODE_LRF, MODE_FLAT, MODE_DEM = "lrf", "flat", "dem:"
ALT_TERRAIN, ALT_PHOTO, ALT_MANUAL = "terrain", "photo", "manual"

_COLOR_ERROR, _COLOR_WARN, _COLOR_OK = "#c0392b", "#b9770e", "#2e7d32"


class _SolveError(Exception):
    pass


def _event_pos(event):
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


def _photo_from_mime(event) -> str | None:
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        path = url.toLocalFile()
        if path.lower().endswith(_PHOTO_SUFFIXES):
            return path
    return None


def _terrain_layers() -> list[QgsRasterLayer]:
    """Einkanalige Raster im Projekt, die als Geländemodell taugen (keine WMS-/Kachel-Karten)."""
    layers = [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if isinstance(layer, QgsRasterLayer)
        and layer.isValid()
        and layer.bandCount() == 1
        and layer.providerType() not in ("wms", "arcgismapserver")
    ]
    return sorted(layers, key=lambda layer: layer.name().lower())


class _LocalFrame:
    """Metrisches Ost/Nord-System mit Ursprung an der Drohne (winkeltreu zur geografischen Nordrichtung)."""

    def __init__(self, lat: float, lon: float):
        self.crs = QgsCoordinateReferenceSystem.fromProj(
            f"+proj=aeqd +lat_0={lat:.9f} +lon_0={lon:.9f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
        )
        if not self.crs.isValid():
            raise _SolveError("Lokales Koordinatensystem um die Drohne konnte nicht erzeugt werden.")
        project = QgsProject.instance()
        self._to_wgs = QgsCoordinateTransform(self.crs, WGS84, project)
        self._from_wgs = QgsCoordinateTransform(WGS84, self.crs, project)

    def to_wgs(self, east: float, north: float) -> QgsPointXY:
        return self._to_wgs.transform(QgsPointXY(east, north))

    def from_wgs(self, lon: float, lat: float) -> tuple[float, float]:
        point = self._from_wgs.transform(QgsPointXY(lon, lat))
        return point.x(), point.y()


class _TerrainSampler:
    """Geländehöhe aus einem Raster-Layer an lokalen Ost/Nord-Koordinaten."""

    def __init__(self, layer: QgsRasterLayer, frame: _LocalFrame):
        self._provider = layer.dataProvider()
        self._transform = QgsCoordinateTransform(frame.crs, layer.crs(), QgsProject.instance())

    def __call__(self, east: float, north: float) -> float | None:
        try:
            point = self._transform.transform(QgsPointXY(east, north))
        except QgsCsException:
            return None
        value, ok = self._provider.sample(point, 1)
        if not ok or value is None or math.isnan(value):
            return None
        return float(value)


class _Crosshair(QGraphicsItem):
    """Fadenkreuz in fester Bildschirmgröße, unabhängig vom Zoom."""

    def __init__(self, color: QColor, radius: float, ring: bool):
        super().__init__()
        self._color = color
        self._radius = radius
        self._ring = ring
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(10)
        path = QPainterPath()
        inner, outer = (radius * 0.4, radius * 1.8) if ring else (radius * 0.3, radius)
        if ring:
            path.addEllipse(QPointF(0, 0), radius, radius)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            path.moveTo(dx * inner, dy * inner)
            path.lineTo(dx * outer, dy * outer)
        self._path = path

    def boundingRect(self) -> QRectF:
        extent = self._radius * 1.8 + 4
        return QRectF(-extent, -extent, 2 * extent, 2 * extent)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 170), 4))
        painter.drawPath(self._path)
        painter.setPen(QPen(self._color, 2))
        painter.drawPath(self._path)


class _PhotoView(QGraphicsView):
    """Fotoanzeige: Klick setzt den Zielpunkt, Ziehen verschiebt, Mausrad zoomt."""

    clicked = pyqtSignal(float, float)  # Pixel im Originalbild
    photo_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(45, 45, 45))
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 320)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._to_original = 1.0
        self._fit = True
        self._press_pos = None
        self._last_pos = None
        self._panning = False

        self._center_mark = _Crosshair(QColor(255, 255, 255, 200), 7, ring=False)
        self._target_mark = _Crosshair(QColor(230, 40, 40), 12, ring=True)
        for item in (self._center_mark, self._target_mark):
            item.hide()
            self.scene().addItem(item)

    def set_image(self, pixmap: QPixmap, to_original: float):
        if self._pixmap_item is not None:
            self.scene().removeItem(self._pixmap_item)
        self._pixmap_item = self.scene().addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._to_original = to_original
        self.setSceneRect(QRectF(pixmap.rect()))
        self.fit()

    def set_marks(self, center: tuple[float, float], target: tuple[float, float] | None):
        self._center_mark.setPos(center[0] / self._to_original, center[1] / self._to_original)
        self._center_mark.show()
        self._target_mark.setVisible(target is not None)
        if target is not None:
            self._target_mark.setPos(target[0] / self._to_original, target[1] / self._to_original)

    def fit(self):
        if self._pixmap_item is None:
            return
        self.resetTransform()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fit = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def wheelEvent(self, event):
        if self._pixmap_item is None:
            return
        factor = 1.25 ** (event.angleDelta().y() / 120)
        scale = self.transform().m11() * factor
        rect = self.sceneRect()
        fit_scale = min(self.viewport().width() / rect.width(), self.viewport().height() / rect.height())
        if factor < 1 and scale <= fit_scale:
            self.fit()
            return
        if factor > 1 and scale > 8:
            return
        self._fit = False
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._press_pos = self._last_pos = _event_pos(event)
            self._panning = event.button() == Qt.MouseButton.MiddleButton
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        pos = _event_pos(event)
        if not self._panning and (pos - self._press_pos).manhattanLength() > 4:
            self._panning = True
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._panning:
            delta = pos - self._last_pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        self._last_pos = pos
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._press_pos is None:
            super().mouseReleaseEvent(event)
            return
        was_click = not self._panning and event.button() == Qt.MouseButton.LeftButton
        self._press_pos = None
        self._panning = False
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        event.accept()
        if was_click and self._pixmap_item is not None:
            scene_pos = self.mapToScene(_event_pos(event))
            if self.sceneRect().contains(scene_pos):
                self.clicked.emit(scene_pos.x() * self._to_original, scene_pos.y() * self._to_original)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap_item is None:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Drohnenfoto (JPG) hierher ziehen\noder „Foto öffnen …“ wählen",
            )
            painter.end()

    def dragEnterEvent(self, event):
        if _photo_from_mime(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = _photo_from_mime(event)
        if not path:
            event.ignore()
            return
        event.acceptProposedAction()
        self.photo_dropped.emit(path)


class PhotoTargetDialog(QDialog):
    """Nicht-modales Fenster: Drohnenfoto laden, Zeichen im Bild anklicken, Zeichen auf der Karte setzen.

    Die Zielkoordinate ergibt sich aus Drohnenposition, Gimbal-Ausrichtung und
    Bildpunkt. Enthält das Foto eine Laser-Messung (DJI M30T, M3TD, Matrice 4T …),
    wird diese für die Bildmitte direkt übernommen. Sonst wird der Sichtstrahl mit
    einer Ebene auf Starthöhe oder mit einem Geländemodell (z. B. DGM1) geschnitten.
    """

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._canvas = plugin.canvas
        self._catalog = symbol_catalog(plugin.plugin_dir)
        self._meta: PhotoMetadata | None = None
        self._pixel: tuple[float, float] | None = None
        self._target: QgsPointXY | None = None  # WGS84
        self._target_text = ""
        self._frame: _LocalFrame | None = None
        self._svg_path: str | None = None
        self._load_warnings: list[str] = []
        self._rubber_band = None
        self._drone_marker = None
        self._target_marker = None
        self._signals_connected = False

        self._recompute_timer = QTimer(self)
        self._recompute_timer.setSingleShot(True)
        self._recompute_timer.setInterval(150)
        self._recompute_timer.timeout.connect(self._recompute)

        self.setWindowTitle("Zeichen aus Drohnenfoto")
        self.resize(1180, 740)
        self._build_ui()
        self._update_result()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.view = _PhotoView()
        self.view.clicked.connect(self._on_photo_clicked)
        self.view.photo_dropped.connect(self.load_photo)
        left_layout.addWidget(self.view)
        view_bar = QHBoxLayout()
        view_hint = QLabel("Klicken: Position des Zeichens im Foto · Ziehen: verschieben · Mausrad: zoomen")
        view_hint.setStyleSheet("QLabel { color: #666; }")
        view_bar.addWidget(view_hint, 1)
        self.center_button = self._button("Bildmitte", self._reset_pixel)
        self.center_button.setToolTip("Zielpunkt auf die Bildmitte setzen (dorthin misst der Laser)")
        view_bar.addWidget(self.center_button)
        view_bar.addWidget(self._button("Ganzes Bild", self.view.fit))
        left_layout.addLayout(view_bar)
        splitter.addWidget(left)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(4, 0, 4, 0)

        file_row = QHBoxLayout()
        file_row.addWidget(self._button("Foto öffnen …", self._choose_photo))
        self.file_label = QLabel("Kein Foto geladen")
        self.file_label.setStyleSheet("QLabel { color: #666; }")
        file_row.addWidget(self.file_label, 1)
        panel_layout.addLayout(file_row)

        # Aufnahme
        shot_box = QGroupBox("Aufnahme")
        shot_form = QFormLayout(shot_box)
        self.camera_label = QLabel("–")
        self.camera_label.setWordWrap(True)
        shot_form.addRow("Kamera:", self.camera_label)
        self.drone_label = QLabel("–")
        self.drone_label.setWordWrap(True)
        self.drone_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        shot_form.addRow("Drohne:", self.drone_label)
        self.yaw_spin = self._spin(-360, 360, 1, " °", "Gimbal-Blickrichtung: 0° = Nord, 90° = Ost")
        shot_form.addRow("Blickrichtung:", self.yaw_spin)
        self.pitch_spin = self._spin(-90, 30, 1, " °", "Gimbal-Neigung: 0° = Horizont, −90° = senkrecht nach unten")
        shot_form.addRow("Neigung:", self.pitch_spin)
        self.focal_spin = self._spin(1, 2000, 1, " mm", "Kleinbild-äquivalente Brennweite (bestimmt den Bildwinkel)")
        self.focal_spin.setValue(24)
        shot_form.addRow("Brennweite (KB):", self.focal_spin)
        panel_layout.addWidget(shot_box)

        # Gelände
        terrain_box = QGroupBox("Gelände am Ziel")
        terrain_layout = QVBoxLayout(terrain_box)
        self.terrain_combo = self._combo()
        self.terrain_combo.currentIndexChanged.connect(self._on_terrain_changed)
        terrain_layout.addWidget(self.terrain_combo)

        self.flat_box = QWidget()
        flat_form = QFormLayout(self.flat_box)
        flat_form.setContentsMargins(0, 0, 0, 0)
        self.height_spin = self._spin(1, 5000, 1, " m", "Höhe der Drohne über dem Gelände am Ziel")
        self.height_spin.setValue(50)
        flat_form.addRow("Höhe über Ziel:", self.height_spin)
        terrain_layout.addWidget(self.flat_box)

        self.dem_box = QWidget()
        dem_form = QFormLayout(self.dem_box)
        dem_form.setContentsMargins(0, 0, 0, 0)
        self.alt_source_combo = self._combo()
        self.alt_source_combo.addItem("Gelände unter Drohne + Höhe über Start", ALT_TERRAIN)
        self.alt_source_combo.addItem("Höhe ü. NN laut Foto", ALT_PHOTO)
        self.alt_source_combo.addItem("Manuell", ALT_MANUAL)
        self.alt_source_combo.setToolTip(
            "Die Höhe ü. NN der Drohne ist bei vielen Modellen um mehrere 10 m ungenau.\n"
            "„Gelände unter Drohne + Höhe über Start“ passt, wenn Start- und Einsatzort etwa gleich hoch liegen."
        )
        self.alt_source_combo.currentIndexChanged.connect(self._on_alt_source_changed)
        dem_form.addRow("Flughöhe aus:", self.alt_source_combo)
        self.alt_spin = self._spin(-500, 9000, 1, " m ü. NN", "Flughöhe im Höhenbezug des Geländemodells")
        self.alt_spin.setEnabled(False)
        dem_form.addRow("Flughöhe:", self.alt_spin)
        terrain_layout.addWidget(self.dem_box)

        self.terrain_hint = QLabel("")
        self.terrain_hint.setWordWrap(True)
        self.terrain_hint.setStyleSheet("QLabel { color: #666; }")
        terrain_layout.addWidget(self.terrain_hint)
        panel_layout.addWidget(terrain_box)

        # Ergebnis
        result_box = QGroupBox("Zielkoordinate")
        result_layout = QVBoxLayout(result_box)
        self.result_label = QLabel("–")
        self.result_label.setStyleSheet("QLabel { font-size: 15px; font-weight: bold; }")
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(self.result_label)
        self.result_detail = QLabel("")
        self.result_detail.setWordWrap(True)
        self.result_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(self.result_detail)
        result_buttons = QHBoxLayout()
        self.copy_button = self._button("Kopieren", self._copy_target)
        self.show_button = self._button("Auf Karte zeigen", self._show_target)
        result_buttons.addWidget(self.copy_button)
        result_buttons.addWidget(self.show_button)
        result_buttons.addStretch()
        result_layout.addLayout(result_buttons)
        panel_layout.addWidget(result_box)

        # Zeichen
        symbol_box = QGroupBox("Taktisches Zeichen")
        symbol_layout = QVBoxLayout(symbol_box)
        self.symbol_button = self._button("Zeichen wählen …", self._pick_symbol)
        self.symbol_button.setIconSize(QSize(32, 32))
        symbol_layout.addWidget(self.symbol_button)
        label_row = QHBoxLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Beschriftung (optional)")
        self.label_edit.returnPressed.connect(self._place_marker)
        label_row.addWidget(self.label_edit, 1)
        self.show_label_check = QCheckBox("anzeigen")
        self.label_edit.textEdited.connect(lambda text: self.show_label_check.setChecked(bool(text.strip())))
        label_row.addWidget(self.show_label_check)
        symbol_layout.addLayout(label_row)
        # Kein Default-Button: Enter in einem Zahlenfeld soll kein Zeichen setzen
        self.place_button = self._button("Zeichen setzen", self._place_marker)
        symbol_layout.addWidget(self.place_button)
        panel_layout.addWidget(symbol_box)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)
        panel_layout.addStretch()

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(self._button("Schließen", self.close))
        panel_layout.addLayout(close_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(360)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([800, 380])

        for spin in (self.yaw_spin, self.pitch_spin, self.focal_spin, self.height_spin, self.alt_spin):
            spin.valueChanged.connect(self._schedule_recompute)
        self._fill_terrain_combo()

    def _button(self, text, slot) -> QPushButton:
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _combo() -> QComboBox:
        combo = QComboBox()
        # Lange Layernamen sollen das Panel nicht verbreitern
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(16)
        return combo

    @staticmethod
    def _spin(minimum, maximum, decimals, suffix, tooltip) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setToolTip(tooltip)
        spin.setKeyboardTracking(False)
        return spin

    # ------------------------------------------------------------------
    # Fenster-Ereignisse
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._signals_connected:
            project = QgsProject.instance()
            project.layersAdded.connect(self._on_layers_changed)
            project.layersRemoved.connect(self._on_layers_changed)
            self._canvas.destinationCrsChanged.connect(self._schedule_recompute)
            self._signals_connected = True
        self._fill_terrain_combo()
        self._schedule_recompute()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._recompute_timer.stop()
        self._clear_map_preview()
        if self._signals_connected:
            project = QgsProject.instance()
            for signal, slot in (
                (project.layersAdded, self._on_layers_changed),
                (project.layersRemoved, self._on_layers_changed),
                (self._canvas.destinationCrsChanged, self._schedule_recompute),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._signals_connected = False

    def _on_layers_changed(self, *_args):
        self._fill_terrain_combo()
        self._schedule_recompute()

    # ------------------------------------------------------------------
    # Foto laden
    # ------------------------------------------------------------------

    def _choose_photo(self):
        settings = QgsSettings()
        start = settings.value(_LAST_DIR_KEY, os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Drohnenfoto öffnen", start, "JPEG-Fotos (*.jpg *.jpeg)")
        if path:
            settings.setValue(_LAST_DIR_KEY, os.path.dirname(path))
            self.load_photo(path)

    def load_photo(self, path: str):
        try:
            meta = read_photo_metadata(path)
        except (OSError, ValueError) as e:
            self._set_status(f"Foto konnte nicht gelesen werden: {e}", _COLOR_ERROR)
            return

        reader = QImageReader(path)
        reader.setAutoTransform(False)  # Sensor-Ausrichtung behalten, sonst passen die Pixel nicht zur Kamera
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > _MAX_DISPLAY:
            reader.setScaledSize(size.scaled(_MAX_DISPLAY, _MAX_DISPLAY, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            self._set_status(f"Foto konnte nicht angezeigt werden: {reader.errorString()}", _COLOR_ERROR)
            return
        if size.isValid() and (meta.width, meta.height) != (size.width(), size.height()):
            meta.width, meta.height = size.width(), size.height()

        self._meta = meta
        self.view.set_image(QPixmap.fromImage(image), meta.width / image.width())
        self.file_label.setText(os.path.basename(path))
        self.file_label.setToolTip(path)
        self._fill_fields(meta)
        self._fill_terrain_combo(reset=True)
        self._pixel = meta.principal_point()
        self._recompute()

    def _fill_fields(self, meta: PhotoMetadata):
        warnings = []
        if meta.gimbal_yaw is None or meta.gimbal_pitch is None:
            warnings.append("Keine Gimbal-Ausrichtung im Foto – Blickrichtung und Neigung von Hand eintragen.")
        if meta.focal_35mm is None:
            warnings.append("Brennweite nicht im Foto – 24 mm angenommen.")
        if meta.rel_alt is None:
            warnings.append("Keine Flughöhe über Start im Foto – Höhe von Hand eintragen.")
        self._load_warnings = warnings

        spins = (self.yaw_spin, self.pitch_spin, self.focal_spin, self.height_spin)
        for spin in spins:
            spin.blockSignals(True)
        self.yaw_spin.setValue(meta.gimbal_yaw if meta.gimbal_yaw is not None else 0.0)
        self.pitch_spin.setValue(meta.gimbal_pitch if meta.gimbal_pitch is not None else -90.0)
        self.focal_spin.setValue(meta.focal_35mm if meta.focal_35mm is not None else 24.0)
        if meta.rel_alt is not None and meta.rel_alt >= 1:
            self.height_spin.setValue(meta.rel_alt)
        for spin in spins:
            spin.blockSignals(False)

        self.camera_label.setText(meta.camera or "unbekannt")
        if not meta.has_position:
            self.drone_label.setText("keine GPS-Position im Foto")
            return
        lines = [self._format_position(meta.lat, meta.lon)]
        heights = []
        if meta.rel_alt is not None:
            heights.append(f"{meta.rel_alt:.0f} m über Start")
        if meta.abs_alt is not None:
            heights.append(f"{meta.abs_alt:.0f} m ü. NN")
        if heights:
            lines.append(" · ".join(heights))
        if meta.has_lrf:
            lines.append(f"Laser: {meta.lrf_distance:.0f} m zur Bildmitte")
        self.drone_label.setText("\n".join(lines))

    # ------------------------------------------------------------------
    # Gelände
    # ------------------------------------------------------------------

    def _fill_terrain_combo(self, reset: bool = False):
        current = None if reset else self.terrain_combo.currentData()
        has_lrf = self._meta is not None and self._meta.has_lrf
        layers = _terrain_layers()

        self.terrain_combo.blockSignals(True)
        self.terrain_combo.clear()
        if has_lrf:
            self.terrain_combo.addItem("Laser-Entfernungsmesser (aus dem Foto)", MODE_LRF)
        self.terrain_combo.addItem("Ebenes Gelände auf Starthöhe", MODE_FLAT)
        for layer in layers:
            self.terrain_combo.addItem(f"Geländemodell: {layer.name()}", MODE_DEM + layer.id())

        index = self.terrain_combo.findData(current) if current else -1
        if index < 0:
            if has_lrf:
                index = 0
            else:
                named = [layer for layer in layers if _DEM_NAME_RE.search(layer.name())]
                index = self.terrain_combo.findData(MODE_DEM + named[0].id()) if named else -1
                if index < 0:
                    index = self.terrain_combo.findData(MODE_FLAT)
        self.terrain_combo.setCurrentIndex(index)
        self.terrain_combo.blockSignals(False)
        self._update_terrain_widgets()

    def _terrain_mode(self) -> str:
        return self.terrain_combo.currentData() or MODE_FLAT

    def _update_terrain_widgets(self):
        mode = self._terrain_mode()
        self.flat_box.setVisible(mode == MODE_FLAT)
        self.dem_box.setVisible(mode.startswith(MODE_DEM))
        self.center_button.setEnabled(self._meta is not None)
        if mode == MODE_LRF:
            hint = (
                "Die Bildmitte ist der Laser-Messpunkt. Andere Bildpunkte werden auf die Höhe "
                "des gemessenen Ziels projiziert."
            )
        elif mode == MODE_FLAT:
            hint = (
                "Annahme: Das Gelände am Ziel liegt so hoch wie der Startpunkt. Bei Hang- oder "
                "Tallage ungenau – dann ein Geländemodell (z. B. DGM1) ins Projekt laden."
            )
        else:
            hint = "Der Sichtstrahl wird mit dem Geländemodell geschnitten (Höhen in m ü. NHN)."
        self.terrain_hint.setText(hint)

    def _on_terrain_changed(self):
        self._update_terrain_widgets()
        self._schedule_recompute()

    def _on_alt_source_changed(self):
        self.alt_spin.setEnabled(self.alt_source_combo.currentData() == ALT_MANUAL)
        self._schedule_recompute()

    def _camera_altitude(self, sampler: _TerrainSampler) -> float:
        """Flughöhe im Höhenbezug des Geländemodells; zeigt den verwendeten Wert im Feld an."""
        source = self.alt_source_combo.currentData()
        meta = self._meta
        if source == ALT_MANUAL:
            return self.alt_spin.value()
        if source == ALT_PHOTO:
            if meta.abs_alt is None:
                raise _SolveError("Das Foto enthält keine Höhe ü. NN – andere Quelle für die Flughöhe wählen.")
            altitude = meta.abs_alt
        else:
            if meta.rel_alt is None:
                raise _SolveError("Das Foto enthält keine Höhe über Start – andere Quelle für die Flughöhe wählen.")
            ground = sampler(0.0, 0.0)
            if ground is None:
                raise _SolveError("Die Drohnenposition liegt außerhalb des Geländemodells.")
            altitude = ground + meta.rel_alt
        self.alt_spin.blockSignals(True)
        self.alt_spin.setValue(altitude)
        self.alt_spin.blockSignals(False)
        return altitude

    # ------------------------------------------------------------------
    # Berechnung
    # ------------------------------------------------------------------

    def _on_photo_clicked(self, x: float, y: float):
        self._pixel = (x, y)
        self._recompute()

    def _reset_pixel(self):
        if self._meta is not None:
            self._pixel = self._meta.principal_point()
            self._recompute()

    def _schedule_recompute(self, *_args):
        self._recompute_timer.start()

    def _recompute(self):
        self._recompute_timer.stop()
        self._target = None
        meta = self._meta
        if meta is None:
            self._update_result()
            return
        self.view.set_marks(meta.principal_point(), self._pixel)
        try:
            east, north, info, warnings = self._solve()
            self._target = self._frame.to_wgs(east, north)
        except _SolveError as e:
            self._update_result(error=str(e))
            self._clear_map_preview()
            return
        except QgsCsException:
            logger.exception("Koordinatenumrechnung für die Zielbestimmung fehlgeschlagen")
            self._update_result(error="Die Koordinate konnte nicht umgerechnet werden.")
            self._clear_map_preview()
            return

        distance = math.hypot(east, north)
        self._target_text = self._format_position(self._target.y(), self._target.x())
        detail = [
            f"{self._target.y():.6f} {self._target.x():.6f}",
            f"{distance:.0f} m in Richtung {azimuth(east, north):.0f}° von der Drohne",
            info,
        ]
        self._update_result(self._target_text, "\n".join(detail), warnings)
        self._update_map_preview(QgsPointXY(meta.lon, meta.lat), self._target)

    def _solve(self) -> tuple[float, float, str, list[str]]:
        """Liefert Ziel (Ost, Nord in m ab Drohne), Beschreibung der Methode und Warnungen."""
        meta = self._meta
        if not meta.has_position:
            raise _SolveError("Das Foto enthält keine GPS-Position der Drohne.")
        if self._pixel is None:
            raise _SolveError("Bitte die Position des Zeichens im Foto anklicken.")
        focal_px = meta.focal_length_px(self.focal_spin.value())
        if not focal_px:
            raise _SolveError("Bildgröße oder Brennweite unbekannt.")

        cx, cy = meta.principal_point()
        u, v = self._pixel
        yaw, pitch, roll = self.yaw_spin.value(), self.pitch_spin.value(), meta.gimbal_roll
        ray = camera_ray(yaw, pitch, roll, (u - cx) / focal_px, (v - cy) / focal_px)
        self._frame = _LocalFrame(meta.lat, meta.lon)

        warnings = list(self._load_warnings)
        mode = self._terrain_mode()
        at_center = math.hypot(u - cx, v - cy) <= max(3.0, 0.003 * meta.width)
        lrf_direct = mode == MODE_LRF and at_center
        if ray[2] >= 0 and not lrf_direct:
            raise _SolveError("Der Bildpunkt liegt über dem Horizont – dort trifft der Sichtstrahl keinen Boden.")
        if ray[2] > -_SHALLOW_RAY and not lrf_direct:
            warnings.append("Sichtstrahl fast waagerecht – schon kleine Winkelfehler verschieben das Ziel stark.")

        if mode == MODE_LRF:
            east, north, info = self._solve_lrf(ray, yaw, pitch, roll, at_center)
        elif mode == MODE_FLAT:
            depth = self.height_spin.value()
            hit = intersect_plane(ray, depth)
            if hit is None:
                raise _SolveError("Der Sichtstrahl trifft den Boden nicht (Blick über den Horizont).")
            east, north = hit[0], hit[1]
            info = f"Ebenes Gelände {depth:.0f} m unter der Drohne"
        else:
            east, north, info = self._solve_dem(ray, mode[len(MODE_DEM) :])
        return east, north, info, warnings

    def _solve_lrf(self, ray, yaw, pitch, roll, at_center) -> tuple[float, float, str]:
        meta = self._meta
        lrf_east, lrf_north = self._frame.from_wgs(meta.lrf_lon, meta.lrf_lat)
        if at_center:
            return lrf_east, lrf_north, f"Laser-Messung ({meta.lrf_distance:.0f} m schräg)"
        # Versatz zur Bildmitte auf der Höhe des gelaserten Ziels
        center_ray = camera_ray(yaw, pitch, roll)
        depth = -center_ray[2] * meta.lrf_distance
        hit, ref = intersect_plane(ray, depth), intersect_plane(center_ray, depth)
        if hit is None or ref is None:
            raise _SolveError("Der Sichtstrahl trifft den Boden nicht (Blick über den Horizont).")
        info = f"Laser-Messung + Versatz auf Zielhöhe ({depth:.0f} m unter der Drohne)"
        return lrf_east + hit[0] - ref[0], lrf_north + hit[1] - ref[1], info

    def _solve_dem(self, ray, layer_id: str) -> tuple[float, float, str]:
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
            raise _SolveError("Das Geländemodell ist nicht mehr im Projekt.")
        sampler = _TerrainSampler(layer, self._frame)
        altitude = self._camera_altitude(sampler)
        ground = sampler(0.0, 0.0)
        if ground is not None and altitude <= ground:
            raise _SolveError(
                f"Flughöhe ({altitude:.0f} m) liegt unter dem Gelände unter der Drohne ({ground:.0f} m) – "
                "Flughöhe prüfen."
            )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            hit = intersect_terrain(ray, altitude, sampler)
        finally:
            QApplication.restoreOverrideCursor()
        if hit is None:
            raise _SolveError(
                "Kein Schnittpunkt mit dem Geländemodell innerhalb von 10 km – Blickrichtung, Neigung "
                "und Flughöhe prüfen oder das Ziel liegt außerhalb des Modells."
            )
        east, north, height, _slant = hit
        return east, north, f"Geländemodell „{layer.name()}“, Zielhöhe {height:.0f} m"

    @staticmethod
    def _format_position(lat: float, lon: float) -> str:
        try:
            return to_mgrs(lat, lon)
        except ValueError:
            return f"{lat:.6f} {lon:.6f}"

    def _update_result(self, text: str = "", detail: str = "", warnings=None, error: str = ""):
        has_target = self._target is not None
        self.result_label.setText(text or "–")
        self.result_detail.setText(detail)
        for button in (self.copy_button, self.show_button, self.place_button):
            button.setEnabled(has_target)
        if error:
            self._set_status(error, _COLOR_ERROR)
        elif warnings:
            self._set_status("\n".join(warnings), _COLOR_WARN)
        elif has_target:
            self._set_status(
                "Ergebnis auf der Karte prüfen: Die Genauigkeit hängt vor allem von Neigung, Kompass und Flughöhe ab.",
                "#666",
            )
        else:
            self._set_status("")

    def _set_status(self, text: str, color: str = _COLOR_ERROR):
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self.status_label.setText(text)

    # ------------------------------------------------------------------
    # Kartenvorschau
    # ------------------------------------------------------------------

    def _update_map_preview(self, drone: QgsPointXY, target: QgsPointXY):
        transform = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            drone_map, target_map = transform.transform(drone), transform.transform(target)
        except QgsCsException:
            self._clear_map_preview()
            return
        if self._rubber_band is None:
            self._rubber_band = QgsRubberBand(self._canvas, _line_geometry_type())
            self._rubber_band.setColor(QColor(230, 40, 40, 200))
            self._rubber_band.setWidth(2)
            self._rubber_band.setLineStyle(Qt.PenStyle.DashLine)
            self._drone_marker = _vertex_marker(self._canvas, "ICON_CIRCLE", QColor(20, 90, 200), 12)
            self._target_marker = _vertex_marker(self._canvas, "ICON_CROSS", QColor(230, 40, 40), 18)
        self._rubber_band.reset(_line_geometry_type())
        self._rubber_band.addPoint(drone_map, False)
        self._rubber_band.addPoint(target_map, True)
        self._drone_marker.setCenter(drone_map)
        self._target_marker.setCenter(target_map)

    def _clear_map_preview(self):
        scene = self._canvas.scene()
        for item in (self._rubber_band, self._drone_marker, self._target_marker):
            if item is not None:
                try:
                    scene.removeItem(item)
                except RuntimeError:
                    pass
        self._rubber_band = self._drone_marker = self._target_marker = None
        self._canvas.refresh()

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def _copy_target(self):
        if self._target is not None:
            QApplication.clipboard().setText(self._target_text)
            self._set_status(f"Kopiert: {self._target_text}", _COLOR_OK)

    def _target_on_map(self) -> QgsPointXY | None:
        transform = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            return transform.transform(self._target)
        except QgsCsException:
            return None

    def _show_target(self):
        point = self._target_on_map() if self._target is not None else None
        if point is not None:
            self._canvas.setCenter(point)
            self._canvas.refresh()

    def _pick_symbol(self) -> bool:
        path = SymbolPickerDialog.pick(self._catalog, self, self._svg_path)
        if not path:
            return False
        self._svg_path = path
        symbol = self._catalog.by_path(path)
        self.symbol_button.setIcon(self._catalog.icon(path))
        self.symbol_button.setText(symbol.name if symbol else os.path.splitext(os.path.basename(path))[0])
        self.symbol_button.setToolTip((symbol.label if symbol else path) + "\nKlicken: anderes Zeichen wählen")
        return True

    def _place_marker(self):
        if self._target is None:
            return
        if not self._svg_path and not self._pick_symbol():
            return
        if self._plugin.layer is None:
            self._plugin.activate()
        label = self.label_edit.text().strip()
        feature = self._plugin.create_marker(
            self._svg_path,
            self._target,
            WGS84,
            label=label or None,
            show_label=self.show_label_check.isChecked(),
        )
        if feature is None:
            self._set_status("Das Zeichen konnte nicht gesetzt werden (Marker-Layer vorhanden?).", _COLOR_ERROR)
            return
        self._show_target()
        self._set_status(f"Zeichen gesetzt: {self._target_text}", _COLOR_OK)


def _line_geometry_type():
    geometry_type = getattr(Qgis, "GeometryType", None)
    if geometry_type is not None:
        return geometry_type.Line
    from qgis.core import QgsWkbTypes  # QGIS < 3.30

    return QgsWkbTypes.LineGeometry


def _vertex_marker(canvas, icon_name: str, color: QColor, size: int) -> QgsVertexMarker:
    marker = QgsVertexMarker(canvas)
    icon = getattr(getattr(QgsVertexMarker, "IconType", None), icon_name, None)
    marker.setIconType(icon if icon is not None else getattr(QgsVertexMarker, icon_name))
    marker.setColor(color)
    marker.setIconSize(size)
    marker.setPenWidth(3)
    return marker
