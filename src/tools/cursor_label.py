from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtWidgets import QLabel

# Offset of the label from the cursor (pixels)
_OFFSET = QPoint(16, 16)


class CursorLabel:
    """Small info box that follows the mouse on the map canvas (e.g. lengths, coordinates).

    Created lazily as a child of the canvas viewport; transparent for mouse events.
    """

    def __init__(self, canvas):
        self._canvas = canvas
        self._label: QLabel | None = None

    def show_at(self, text: str, pos: QPoint):
        """Show ``text`` next to the cursor position ``pos`` (viewport pixels)."""
        label = self._ensure_label()
        label.setText(text)
        label.adjustSize()
        # Keep the label inside the canvas: flip to the other side of the cursor near the edges
        viewport = self._canvas.viewport()
        x = pos.x() + _OFFSET.x()
        y = pos.y() + _OFFSET.y()
        if x + label.width() > viewport.width():
            x = pos.x() - _OFFSET.x() - label.width()
        if y + label.height() > viewport.height():
            y = pos.y() - _OFFSET.y() - label.height()
        label.move(x, y)
        label.show()
        label.raise_()

    def hide(self):
        if self._label is not None:
            self._label.hide()

    def dispose(self):
        if self._label is not None:
            self._label.deleteLater()
            self._label = None

    def _ensure_label(self) -> QLabel:
        if self._label is None:
            label = QLabel(self._canvas.viewport())
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setStyleSheet(
                "QLabel { background: rgba(255, 255, 255, 230); color: black; border: 1px solid #666;"
                " border-radius: 3px; padding: 2px 5px; }"
            )
            self._label = label
        return self._label
