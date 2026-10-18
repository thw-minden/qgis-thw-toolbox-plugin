"""Schriftgröße in pt aus einer Liste wählen oder eintippen, wie in Textprogrammen."""

from qgis.PyQt.QtCore import QRegularExpression, pyqtSignal
from qgis.PyQt.QtGui import QRegularExpressionValidator
from qgis.PyQt.QtWidgets import QComboBox

_SIZES = (6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72)
MIN_PT = 4.0
MAX_PT = 200.0


def _text(size: float | None) -> str:
    return "" if size is None else f"{size:g}".replace(".", ",")


class FontSizeBox(QComboBox):
    """``size_chosen`` meldet eine neu gewählte Größe (pt) – nie beim bloßen Anzeigen."""

    size_chosen = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItems([_text(size) for size in _SIZES])
        self.lineEdit().setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,3}([.,]\d)?"), self))
        self.setToolTip("Schriftgröße der Beschriftung in Punkt (pt)")
        self._shown: float | None = None
        self.activated.connect(self._commit)
        self.lineEdit().editingFinished.connect(self._commit)

    def set_sizes(self, sizes: list[float]) -> None:
        """Größe(n) der Auswahl anzeigen; unterschiedliche lassen das Feld leer."""
        distinct = {round(size, 1) for size in sizes}
        self._shown = next(iter(distinct)) if len(distinct) == 1 else None
        self.blockSignals(True)
        self.setEditText(_text(self._shown))
        self.lineEdit().setPlaceholderText("unterschiedlich" if len(distinct) > 1 else "")
        self.blockSignals(False)

    def _commit(self, *_args):
        try:
            size = float(self.currentText().strip().replace(",", "."))
        except ValueError:
            # Leer oder unvollständig: wieder die bisherige Größe zeigen
            self.setEditText(_text(self._shown))
            return
        size = round(min(MAX_PT, max(MIN_PT, size)), 1)
        self.setEditText(_text(size))
        if size == self._shown:
            return
        self._shown = size
        self.size_chosen.emit(size)
