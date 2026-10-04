import os
from collections.abc import Callable

import processing
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayer,
    QgsProcessingFeedback,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..logging_utils import get_logger

logger = get_logger(__name__)

_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
_DEFAULT_ZOOM_MIN = 14
_DEFAULT_ZOOM_MAX = 19


class _ProgressFeedback(QgsProcessingFeedback):
    def __init__(
        self,
        on_update: Callable[[int, str], None] | None,
        on_cancel_check: Callable[[], bool] | None,
    ):
        super().__init__()
        self._on_update = on_update
        self._on_cancel_check = on_cancel_check
        self._last_text = ""

    def _maybe_cancel(self):
        if self._on_cancel_check and self._on_cancel_check():
            self.cancel()

    def setProgress(self, progress: float):
        super().setProgress(progress)
        if self._on_update:
            self._on_update(int(progress), self._last_text)
        self._maybe_cancel()

    def setProgressText(self, text: str):
        super().setProgressText(text)
        self._last_text = text or ""
        if self._on_update:
            self._on_update(int(self.progress()), self._last_text)
        self._maybe_cancel()


class DjiMbtilesExporter:
    def __init__(
        self,
        on_success: Callable[[str], None],
        on_error: Callable[[str, str, str], None],
        on_progress: Callable[[str], None] | None = None,
        on_feedback: Callable[[int, str], None] | None = None,
        on_cancel_check: Callable[[], bool] | None = None,
    ):
        self._on_success = on_success
        self._on_error = on_error
        self._on_progress = on_progress or (lambda _msg: None)
        self._on_feedback = on_feedback
        self._on_cancel_check = on_cancel_check

    def prompt_and_export(self, layer: QgsVectorLayer, parent_widget) -> bool:
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            self._on_error(
                "Ebenenlayer-Export (Drohne)",
                "Ungültiger Layer",
                "Der gewählte Layer ist kein gültiger Vektorlayer.",
            )
            return False

        params = _ZoomDialog.ask(parent_widget)
        if params is None:
            return False
        zoom_min, zoom_max = params

        default_name = _safe_filename(layer.name()) + ".mbtiles"
        initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")
        initial_path = os.path.join(initial_dir, default_name)

        target_path, _ = QFileDialog.getSaveFileName(
            parent_widget,
            "Ebenenlayer (MBTiles) für Drohne speichern",
            initial_path,
            "MBTiles (*.mbtiles)",
        )
        if not target_path:
            return False
        if not target_path.lower().endswith(".mbtiles"):
            target_path += ".mbtiles"

        return self.export(layer, target_path, zoom_min, zoom_max)

    def export(
        self,
        layer: QgsMapLayer,
        target_path: str,
        zoom_min: int,
        zoom_max: int,
        extent: QgsRectangle | None = None,
    ) -> bool:
        """Render ``layer`` (any map layer in the layer tree) into MBTiles.

        ``extent`` (layer CRS) overrides the layer's own extent — e.g. for annotation
        layers, whose extent ignores radius circles and texts.
        """
        extent_4326 = _layer_extent_in_wgs84(layer, extent)
        if extent_4326 is None or extent_4326.isEmpty():
            self._on_error(
                "Ebenenlayer-Export (Drohne)",
                "Layer hat keine verwertbare Ausdehnung",
                "Der Layer enthält keine Features oder die Ausdehnung ist leer.",
            )
            return False

        self._on_progress(
            f"Erzeuge MBTiles für „{layer.name()}“ (Zoom {zoom_min}–{zoom_max}) — das kann einige Minuten dauern …"
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # The `native:tilesxyzmbtiles` algorithm renders whatever is visible
        # in the layer tree — MAP_THEME is not reliably honored across QGIS
        # versions. So we solo the target layer by toggling visibility
        # directly and restore the previous state afterwards.
        previous_visibility = _solo_layer_visibility(layer)
        try:
            params = {
                "EXTENT": (
                    f"{extent_4326.xMinimum()},{extent_4326.xMaximum()},"
                    f"{extent_4326.yMinimum()},{extent_4326.yMaximum()} [EPSG:4326]"
                ),
                "ZOOM_MIN": zoom_min,
                "ZOOM_MAX": zoom_max,
                "DPI": 96,
                "BACKGROUND_COLOR": QColor(0, 0, 0, 0),  # fully transparent
                "ANTIALIAS": True,
                "TILE_FORMAT": 0,  # 0 = PNG (supports alpha), 1 = JPG (no alpha)
                "QUALITY": 75,
                "METATILESIZE": 4,
                "OUTPUT_FILE": target_path,
            }

            logger.info(
                "MBTiles-Export gestartet: %s (Zoom %d-%d, Solo-Layer %s)",
                target_path,
                zoom_min,
                zoom_max,
                layer.id(),
            )
            feedback = _ProgressFeedback(self._on_feedback, self._on_cancel_check)
            processing.run("native:tilesxyzmbtiles", params, feedback=feedback)
        except Exception as e:
            logger.exception("MBTiles-Export fehlgeschlagen")
            self._on_error(
                "Ebenenlayer-Export (Drohne)",
                "Export fehlgeschlagen",
                f"Ziel: {target_path}\nFehler: {e}",
            )
            return False
        finally:
            _restore_layer_visibility(previous_visibility)
            QApplication.restoreOverrideCursor()

        self._on_success(target_path)
        return True


def _solo_layer_visibility(layer: QgsMapLayer) -> dict[str, bool]:
    """Hide every layer in the tree except `layer`; return previous state."""
    root = QgsProject.instance().layerTreeRoot()
    previous = {n.layerId(): n.isVisible() for n in root.findLayers()}
    for node in root.findLayers():
        node.setItemVisibilityChecked(node.layerId() == layer.id())
    return previous


def _restore_layer_visibility(previous: dict[str, bool]) -> None:
    try:
        root = QgsProject.instance().layerTreeRoot()
        for node in root.findLayers():
            if node.layerId() in previous:
                node.setItemVisibilityChecked(previous[node.layerId()])
    except Exception as e:
        logger.warning("Konnte Layer-Sichtbarkeit nicht zurücksetzen: %s", e)


def _layer_extent_in_wgs84(layer: QgsMapLayer, extent: QgsRectangle | None = None) -> QgsRectangle | None:
    extent = extent if extent is not None else layer.extent()
    if extent.isEmpty():
        return None
    src_crs = layer.crs()
    if not src_crs.isValid() or src_crs == _WGS84:
        return extent
    transform = QgsCoordinateTransform(src_crs, _WGS84, QgsProject.instance())
    try:
        return transform.transformBoundingBox(extent)
    except Exception as e:
        logger.warning("Extent-Transform fehlgeschlagen: %s", e)
        return None


def _safe_filename(name: str) -> str:
    keep = "-_. "
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()
    return cleaned or "layer"


class _ZoomDialog(QDialog):
    """Simple modal to collect zoom-min / zoom-max before running the heavy export."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("MBTiles-Zoomstufen")
        # Without a minimum width Qt shrinks the dialog to the spin boxes, which cuts off
        # the window title and the wrapped info text
        self.setMinimumWidth(420)

        self.min_spin = QSpinBox(self)
        self.min_spin.setRange(0, 22)
        self.min_spin.setValue(_DEFAULT_ZOOM_MIN)

        self.max_spin = QSpinBox(self)
        self.max_spin.setRange(0, 22)
        self.max_spin.setValue(_DEFAULT_ZOOM_MAX)

        form = QFormLayout()
        form.addRow("Min. Zoom:", self.min_spin)
        form.addRow("Max. Zoom:", self.max_spin)

        info = QLabel(
            "Höhere Zoomstufen = mehr Detail, aber größere Datei und längere "
            "Rechenzeit. Default 14-19 ist für Einsatzkarten üblich."
        )
        info.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(buttons)
        # Height must follow the wrapped text at the final width
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self.adjustSize()

    @classmethod
    def ask(cls, parent) -> tuple[int, int] | None:
        dlg = cls(parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        zmin = dlg.min_spin.value()
        zmax = dlg.max_spin.value()
        if zmax < zmin:
            zmin, zmax = zmax, zmin
        return zmin, zmax
