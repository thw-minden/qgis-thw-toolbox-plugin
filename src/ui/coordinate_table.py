"""Koordinaten der Punkte eines Objekts als Tabelle: UTMREF und Breite/Länge, auch zum Eintippen.

Eine Zelle übernimmt UTMREF wie Breite/Länge (``util.coordinates.parse_position``). Punkte lassen sich
einfügen (mittig zum nächsten) und löschen, solange die Mindestanzahl bleibt; „Kopieren“ legt alle
Zeilen tabulatorgetrennt in die Zwischenablage. Jede gültige Änderung meldet ``edited`` sofort.
"""

from qgis.core import QgsPointXY
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..util.coordinates import parse_position, to_mgrs

_COLUMNS = ("UTMREF", "Breite Länge")
_INVALID = QBrush(QColor(198, 40, 40))


def _texts(point: QgsPointXY) -> tuple[str, str]:
    try:
        utmref = to_mgrs(point.y(), point.x())
    except ValueError:
        utmref = "–"
    return utmref, f"{point.y():.6f} {point.x():.6f}"


class CoordinateTable(QWidget):
    """``edited(list[QgsPointXY])``: alle Punkte (WGS 84) nach einer gültigen Änderung."""

    edited = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[QgsPointXY] = []
        self._min_points = 1
        self._closed = False
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setToolTip("Zelle doppelt anklicken und UTMREF oder „Breite Länge“ eintippen")
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._table)

        row = QHBoxLayout()
        self._insert = QPushButton("Punkt einfügen")
        self._insert.setToolTip("Neuen Punkt mittig zwischen dem markierten und dem nächsten einfügen")
        self._insert.clicked.connect(self._on_insert)
        self._delete = QPushButton("Punkt löschen")
        self._delete.clicked.connect(self._on_delete)
        copy = QPushButton("Kopieren")
        copy.setToolTip("Alle Punkte (Nummer, UTMREF, Breite/Länge) in die Zwischenablage kopieren")
        copy.clicked.connect(self._on_copy)
        for button in (self._insert, self._delete, copy):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

    def set_points(self, points: list[QgsPointXY], min_points: int, closed: bool = False):
        """Anzeigen, ohne ``edited`` auszulösen. ``min_points`` 1 (Punkt, Text): ohne Einfügen und Löschen."""
        same = len(points) == len(self._points) and all(p.compare(q, 1e-9) for p, q in zip(points, self._points))
        self._min_points = min_points
        self._closed = closed
        self._insert.setVisible(min_points > 1)
        self._delete.setVisible(min_points > 1)
        if same and self._table.rowCount() == len(points):
            self._update_buttons()
            return
        self._points = [QgsPointXY(p) for p in points]
        self._fill()

    def _fill(self):
        self._filling = True
        self._table.setRowCount(len(self._points))
        for row, point in enumerate(self._points):
            for col, text in enumerate(_texts(point)):
                item = QTableWidgetItem(text)
                item.setToolTip("")
                self._table.setItem(row, col, item)
        self._table.setVerticalHeaderLabels([str(i + 1) for i in range(len(self._points))])
        rows = min(len(self._points), 8)
        header = self._table.horizontalHeader().height()
        self._table.setFixedHeight(header + rows * self._table.verticalHeader().defaultSectionSize() + 4)
        self._filling = False
        self._update_buttons()

    def _update_buttons(self):
        current = self._table.currentRow()
        self._insert.setEnabled(current >= 0 and (self._closed or current < len(self._points) - 1))
        self._delete.setEnabled(current >= 0 and len(self._points) > self._min_points)

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._filling:
            return
        try:
            point = parse_position(item.text())
        except ValueError as e:
            # Ungültig: rot markieren, der Punkt bleibt, wo er ist
            self._filling = True
            item.setForeground(_INVALID)
            item.setToolTip(str(e))
            self._filling = False
            return
        self._points[item.row()] = point
        # Erst nach dem Signal neu aufbauen – die Tabelle steckt noch in ihrer eigenen Änderung
        QTimer.singleShot(0, self._apply_edit)

    def _apply_edit(self):
        self._fill()
        self.edited.emit(list(self._points))

    def _on_insert(self):
        row = self._table.currentRow()
        if row < 0:
            return
        a = self._points[row]
        b = self._points[(row + 1) % len(self._points)]
        self._points.insert(row + 1, QgsPointXY((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0))
        self._fill()
        self._table.selectRow(row + 1)
        self.edited.emit(list(self._points))

    def _on_delete(self):
        row = self._table.currentRow()
        if row < 0 or len(self._points) <= self._min_points:
            return
        del self._points[row]
        self._fill()
        self.edited.emit(list(self._points))

    def _on_copy(self):
        lines = [f"{i + 1}\t{utmref}\t{latlon}" for i, (utmref, latlon) in enumerate(map(_texts, self._points))]
        QApplication.clipboard().setText("\n".join(lines))
