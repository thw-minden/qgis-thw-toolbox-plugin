from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY
from qgis.gui import QgsCollapsibleGroupBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..layout.mgrs_grid import mgrs_to_point, point_to_mgrs

# Per row: the exact position (layer CRS) and the text it was shown as. A row whose text is
# unchanged keeps its exact position — re-reading the text would snap it to the MGRS cell corner.
_POINT_ROLE = Qt.ItemDataRole.UserRole
_ORIGINAL_TEXT_ROLE = Qt.ItemDataRole.UserRole + 1

_INVALID_BRUSH = QBrush(QColor(255, 200, 200))

# Row numbers live in a normal first column (not the vertical header), so they share the
# row's background and selection highlight
_NUMBER_COL = 0
_COORD_COL = 1


class InvalidCoordinateError(ValueError):
    """Raised by ``CoordinateBox.points`` for a row that is not a valid MGRS coordinate."""

    def __init__(self, row: int, text: str):
        super().__init__(f"Zeile {row + 1}: „{text}“ ist keine gültige MGRS-Koordinate.")
        self.row = row


class CoordinateBox(QgsCollapsibleGroupBox):
    """Collapsible list of an object's vertices as MGRS coordinates, with an optional edit mode.

    In edit mode the coordinates can be typed in, and for lines/polygons vertices can be
    inserted between two others or removed — but never below ``min_count``.
    """

    def __init__(
        self,
        points: list[QgsPointXY],
        crs: QgsCoordinateReferenceSystem,
        mgrs_resolution_m: float,
        min_count: int,
        closed: bool,
        parent=None,
    ):
        super().__init__("Koordinaten (MGRS)", parent)
        self._crs = crs
        self._resolution = mgrs_resolution_m
        self._min_count = min_count
        self._closed = closed  # polygon: the last vertex connects back to the first
        self._can_change_count = min_count > 1  # a point has exactly one vertex
        self._changed = False

        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 2)
        self._table.horizontalHeader().setVisible(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(_NUMBER_COL, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COORD_COL, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        self._edit_btn = QPushButton("Bearbeiten")
        self._edit_btn.setCheckable(True)
        self._edit_btn.toggled.connect(self._set_edit_mode)
        buttons.addWidget(self._edit_btn)
        self._insert_btn = QPushButton("Punkt einfügen")
        self._insert_btn.setToolTip("Fügt nach der markierten Zeile einen Punkt in der Mitte der Kante ein")
        self._insert_btn.clicked.connect(self._insert_after_current)
        buttons.addWidget(self._insert_btn)
        self._delete_btn = QPushButton("Punkt löschen")
        self._delete_btn.clicked.connect(self._delete_current)
        buttons.addWidget(self._delete_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        for point in points:
            self._append_row(point)
        rows_visible = min(max(len(points), 1), 8)
        self._table.setFixedHeight(self._table.verticalHeader().defaultSectionSize() * rows_visible + 6)

        self._set_edit_mode(False)
        self.setCollapsed(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_changes(self) -> bool:
        return self._changed

    def points(self) -> list[QgsPointXY]:
        """Current vertices (layer CRS). Raises ``InvalidCoordinateError`` for an unparsable row."""
        return [self._row_point(row) for row in range(self._table.rowCount())]

    def mark_invalid(self, row: int):
        """Open the box and highlight ``row`` (used by the dialog when validation fails)."""
        self.setCollapsed(False)
        self._edit_btn.setChecked(True)
        self._set_valid_style(self._table.item(row, _COORD_COL), False)
        self._table.selectRow(row)
        self._table.scrollToItem(self._table.item(row, _COORD_COL))

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _format(self, point: QgsPointXY) -> str:
        return point_to_mgrs(point, self._crs, self._resolution) or "(außerhalb UTM)"

    def _append_row(self, point: QgsPointXY, at: int | None = None):
        row = self._table.rowCount() if at is None else at
        text = self._format(point)
        item = QTableWidgetItem(text)
        item.setData(_POINT_ROLE, point)
        item.setData(_ORIGINAL_TEXT_ROLE, text)
        self._table.blockSignals(True)
        self._table.insertRow(row)
        self._table.setItem(row, _COORD_COL, item)
        self._table.blockSignals(False)
        self._renumber()

    def _renumber(self):
        """(Re)write the row numbers after rows were inserted or removed."""
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            number = QTableWidgetItem(str(row + 1))
            number.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)  # never editable
            number.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, _NUMBER_COL, number)
        self._table.blockSignals(False)

    def _row_point(self, row: int) -> QgsPointXY:
        item = self._table.item(row, _COORD_COL)
        text = item.text().strip()
        if text == item.data(_ORIGINAL_TEXT_ROLE):
            return item.data(_POINT_ROLE)
        point = mgrs_to_point(text, self._crs)
        if point is None:
            raise InvalidCoordinateError(row, text)
        return point

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() != _COORD_COL:
            return
        self._changed = True
        valid = item.text().strip() == item.data(_ORIGINAL_TEXT_ROLE) or (
            mgrs_to_point(item.text(), self._crs) is not None
        )
        self._set_valid_style(item, valid)

    def _set_valid_style(self, item: QTableWidgetItem, valid: bool):
        # A background change emits itemChanged as well — keep it from counting as an edit
        self._table.blockSignals(True)
        item.setBackground(QBrush() if valid else _INVALID_BRUSH)
        self._table.blockSignals(False)

    def _insert_after_current(self):
        row = self._table.currentRow()
        count = self._table.rowCount()
        if row < 0 or count < 2:
            return
        # Insert into the edge after the selected vertex; for an open line's last vertex, into the edge before it
        if row == count - 1 and not self._closed:
            row -= 1
        try:
            a = self._row_point(row)
            b = self._row_point((row + 1) % count)
        except InvalidCoordinateError as e:
            self.mark_invalid(e.row)
            self._hint.setText(str(e))
            return
        self._append_row(QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2), at=row + 1)
        self._changed = True
        self._table.selectRow(row + 1)
        self._update_buttons()

    def _delete_current(self):
        row = self._table.currentRow()
        if row < 0 or self._table.rowCount() <= self._min_count:
            return
        self._table.removeRow(row)
        self._renumber()
        self._changed = True
        self._update_buttons()

    # ------------------------------------------------------------------
    # Modes / buttons
    # ------------------------------------------------------------------

    def _set_edit_mode(self, editing: bool):
        triggers = (
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
            if editing
            else QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.setEditTriggers(triggers)
        self._insert_btn.setVisible(editing and self._can_change_count)
        self._delete_btn.setVisible(editing and self._can_change_count)
        self._update_buttons()

    def _update_buttons(self):
        editing = self._edit_btn.isChecked()
        has_row = self._table.currentRow() >= 0
        at_minimum = self._table.rowCount() <= self._min_count
        self._insert_btn.setEnabled(editing and has_row)
        self._delete_btn.setEnabled(editing and has_row and not at_minimum)
        if not editing:
            self._hint.setText("")
        elif at_minimum and self._can_change_count:
            self._hint.setText(f"Mindestens {self._min_count} Punkte erforderlich – Löschen nicht möglich.")
        else:
            self._hint.setText("Doppelklick auf eine Koordinate zum Ändern, z. B. „32U MB 12345 98765“.")
