from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY
from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QKeySequence
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

try:  # Qt6 (QGIS 4)
    from qgis.PyQt.QtGui import QShortcut
except ImportError:  # Qt5 (QGIS 3)
    from qgis.PyQt.QtWidgets import QShortcut

from ..layer.annotations import (
    ARROWS_BOTH,
    ARROWS_END,
    ARROWS_NONE,
    ARROWS_START,
    KIND_LINE,
    KIND_POINT,
    KIND_POLYGON,
    KIND_TEXT,
    POSITION_PLACEHOLDER,
    AnnotationEntry,
    Measurements,
    default_radius_fill,
    min_vertices,
)
from ..util.units import format_area, format_meters
from .config_dialog import line_width_spinbox
from .coordinate_box import CoordinateBox, InvalidCoordinateError

_DIMENSION_LABELS = {
    KIND_POINT: "Radius auf der Karte anzeigen",
    KIND_LINE: "Segmentlängen auf der Karte anzeigen",
    KIND_POLYGON: "Kantenlängen auf der Karte anzeigen",
}

_TEXT_LABELS = {
    KIND_TEXT: "Text (auf der Karte)",
}


class AnnotationEditDialog(QDialog):
    """Edit text and colors of one annotation object, or delete it.

    After ``exec()`` returns Accepted, either ``deleted`` is True or the
    new values are available via ``name()``, ``line_color()``, ``line_width()``, ``radius_m()``,
    ``fill_color()`` and ``edited_vertices()``.
    """

    def __init__(
        self,
        entry: AnnotationEntry,
        default_fill_color: QColor,
        measurements: Measurements | None = None,
        parent=None,
        vertices: list[QgsPointXY] | None = None,
        crs: QgsCoordinateReferenceSystem | None = None,
        mgrs_resolution_m: float = 1.0,
    ):
        super().__init__(parent)
        self.deleted = False
        self._edited_vertices: list[QgsPointXY] | None = None
        self._kind = entry.kind
        self.setWindowTitle(f"{entry.short_title} bearbeiten")
        # Wide enough for the coordinate box's three buttons side by side
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        # Label above the text field so the field can use the full dialog width
        self._name_edit = description_edit(entry.name, for_point=entry.kind == KIND_POINT)
        name_label = QLabel(_TEXT_LABELS.get(entry.kind, "Beschreibung (auf der Karte)"))
        name_label.setBuddy(self._name_edit)
        layout.addWidget(name_label)
        layout.addWidget(self._name_edit)
        layout.addSpacing(8)

        form = QFormLayout()
        layout.addLayout(form)

        self._line_color_btn = _color_button(self, "Farbe", entry.line_color)
        color_label = "Linienfarbe" if entry.kind not in (KIND_POINT, KIND_TEXT) else "Farbe"
        form.addRow(color_label, self._line_color_btn)

        self._line_width_spin = None
        if entry.kind in (KIND_LINE, KIND_POLYGON):
            self._line_width_spin = line_width_spinbox(entry.line_width)
            form.addRow("Linienbreite", self._line_width_spin)

        self._arrows_combo = None
        self._arrow_size_spin = None
        if entry.kind == KIND_LINE:
            self._arrows_combo = QComboBox()
            for mode, label in (
                (ARROWS_NONE, "Kein Pfeil"),
                (ARROWS_END, "Pfeil am Ende"),
                (ARROWS_START, "Pfeil am Anfang"),
                (ARROWS_BOTH, "Pfeile an beiden Enden"),
            ):
                self._arrows_combo.addItem(label, mode)
            self._arrows_combo.setCurrentIndex(max(0, self._arrows_combo.findData(entry.arrows)))
            self._arrows_combo.setToolTip("Ende = letzter gezeichneter Punkt, Anfang = erster Punkt")
            form.addRow("Pfeilspitzen", self._arrows_combo)

            self._arrow_size_spin = QDoubleSpinBox()
            self._arrow_size_spin.setDecimals(1)
            self._arrow_size_spin.setRange(0.0, 30.0)
            self._arrow_size_spin.setSingleStep(0.5)
            self._arrow_size_spin.setSuffix(" mm")
            # 0 = automatic: grows with the line width
            self._arrow_size_spin.setSpecialValueText("automatisch")
            self._arrow_size_spin.setValue(entry.arrow_size_mm)
            self._arrow_size_spin.setToolTip("Größe der Pfeilspitzen; „automatisch“ richtet sich nach der Linienbreite")
            form.addRow("Pfeilgröße", self._arrow_size_spin)
            self._arrow_size_spin.setEnabled(entry.arrows != ARROWS_NONE)
            self._arrows_combo.currentIndexChanged.connect(
                lambda _i: self._arrow_size_spin.setEnabled(self._arrows_combo.currentData() != ARROWS_NONE)
            )

        self._radius_spin = None
        self._fill_check = None
        self._fill_color_btn = None
        self._fill_follows_color = False  # point radius fill tracks the point color until picked by hand
        self._updating_fill = False
        if entry.kind == KIND_POINT:
            self._radius_spin = radius_spinbox(entry.radius_m or 0.0)
            form.addRow("Radius", self._radius_spin)

            # Circle fill: only meaningful with a radius. Until the user picks a fill
            # explicitly, it follows the point color (mostly transparent).
            self._fill_follows_color = entry.fill_color is None or entry.fill_color == default_radius_fill(
                entry.line_color
            )
            self._fill_color_btn = _color_button(
                self, "Füllfarbe Radius", entry.fill_color or default_radius_fill(entry.line_color)
            )
            self._fill_color_btn.setEnabled(self._radius_spin.value() > 0)
            self._radius_spin.valueChanged.connect(lambda value: self._fill_color_btn.setEnabled(value > 0))
            self._line_color_btn.colorChanged.connect(self._on_point_color_changed)
            self._fill_color_btn.colorChanged.connect(self._on_radius_fill_changed)
            form.addRow("Füllfarbe Radius", self._fill_color_btn)

        self._dimensions_check = None
        if entry.kind in _DIMENSION_LABELS:
            self._dimensions_check = QCheckBox(_DIMENSION_LABELS[entry.kind])
            self._dimensions_check.setChecked(entry.show_dimensions)
            form.addRow("", self._dimensions_check)
            if entry.kind == KIND_POINT:
                # Without a radius there is nothing to show
                self._dimensions_check.setEnabled(self._radius_spin.value() > 0)
                self._radius_spin.valueChanged.connect(lambda value: self._dimensions_check.setEnabled(value > 0))

        self._dimension_size_spin = None
        if entry.kind in (KIND_LINE, KIND_POLYGON):
            self._dimension_size_spin = QDoubleSpinBox()
            self._dimension_size_spin.setDecimals(1)
            self._dimension_size_spin.setRange(1.0, 20.0)
            self._dimension_size_spin.setSingleStep(0.5)
            self._dimension_size_spin.setSuffix(" mm")
            self._dimension_size_spin.setValue(entry.dimension_size_mm)
            self._dimension_size_spin.setToolTip(
                "Textgröße der Längenbeschriftungen. Eine Änderung setzt von Hand skalierte Beschriftungen "
                "auf diese Größe zurück (verschobene Positionen bleiben erhalten)."
            )
            form.addRow("Größe Längenbeschriftung", self._dimension_size_spin)
            self._dimension_size_spin.setEnabled(self._dimensions_check.isChecked())
            self._dimensions_check.toggled.connect(self._dimension_size_spin.setEnabled)

        if entry.kind == KIND_POLYGON:
            self._fill_check = QCheckBox("Fläche füllen")
            self._fill_check.setChecked(entry.fill_color is not None)
            self._fill_color_btn = _color_button(self, "Füllfarbe", entry.fill_color or default_fill_color)
            self._fill_color_btn.setEnabled(self._fill_check.isChecked())
            self._fill_check.toggled.connect(self._fill_color_btn.setEnabled)
            form.addRow("", self._fill_check)
            form.addRow("Füllfarbe", self._fill_color_btn)

        measurement_box = _measurement_box(measurements)
        if measurement_box is not None:
            layout.addWidget(measurement_box)

        # Vertices as MGRS coordinates — collapsed by default, editable on demand
        self._coordinate_box = None
        if vertices and crs is not None and min_vertices(entry.kind):
            self._coordinate_box = CoordinateBox(
                vertices,
                crs,
                mgrs_resolution_m,
                min_vertices(entry.kind),
                closed=entry.kind == KIND_POLYGON,
                parent=self,
            )
            layout.addWidget(self._coordinate_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        delete_btn = buttons.addButton("Löschen", QDialogButtonBox.ButtonRole.DestructiveRole)
        delete_btn.clicked.connect(self._confirm_delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        _add_confirm_shortcut(self)

    def accept(self):
        """Validate edited coordinates before closing; an invalid row keeps the dialog open."""
        if not self.deleted and self._coordinate_box is not None and self._coordinate_box.has_changes():
            try:
                self._edited_vertices = self._coordinate_box.points()
            except InvalidCoordinateError as e:
                self._coordinate_box.mark_invalid(e.row)
                QMessageBox.warning(self, "Ungültige Koordinate", str(e))
                return
        super().accept()

    def edited_vertices(self) -> list[QgsPointXY] | None:
        """New vertices (layer CRS) if the coordinates were edited, else None."""
        return self._edited_vertices

    def name(self) -> str:
        return self._name_edit.toPlainText()

    def line_color(self) -> QColor:
        return self._line_color_btn.color()

    def radius_m(self) -> float | None:
        """Radius circle in meters (0 = none), or None for objects other than points."""
        return self._radius_spin.value() if self._radius_spin is not None else None

    def show_dimensions(self) -> bool | None:
        """Whether radius / edge lengths are shown on the map; None for texts."""
        return self._dimensions_check.isChecked() if self._dimensions_check is not None else None

    def arrows(self) -> str | None:
        """Arrowhead mode (ARROWS_*) of a line; None for other objects."""
        return self._arrows_combo.currentData() if self._arrows_combo is not None else None

    def arrow_size_mm(self) -> float | None:
        """Arrowhead size in mm (0 = automatic); None for objects other than lines."""
        return self._arrow_size_spin.value() if self._arrow_size_spin is not None else None

    def dimension_size_mm(self) -> float | None:
        """Text size of the length labels in mm; None for objects without edge lengths."""
        return self._dimension_size_spin.value() if self._dimension_size_spin is not None else None

    def line_width(self) -> float | None:
        """Line width in mm, or None for objects without a line (points, texts)."""
        return self._line_width_spin.value() if self._line_width_spin is not None else None

    def fill_color(self) -> QColor | None:
        """Polygon fill (None if unfilled) or a point's radius circle fill (None without radius)."""
        if self._kind == KIND_POINT:
            return self._fill_color_btn.color() if self._radius_spin.value() > 0 else None
        if self._fill_check is None or not self._fill_check.isChecked():
            return None
        return self._fill_color_btn.color()

    def _on_point_color_changed(self, color: QColor):
        if self._fill_follows_color:
            self._updating_fill = True
            self._fill_color_btn.setColor(default_radius_fill(color))
            self._updating_fill = False

    def _on_radius_fill_changed(self, _color: QColor):
        # A fill picked by the user no longer follows the point color
        if not self._updating_fill:
            self._fill_follows_color = False

    def _confirm_delete(self):
        answer = QMessageBox.question(
            self,
            "Annotation löschen",
            "Soll dieses Objekt wirklich von der Karte entfernt werden?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.accept()


class PointCreateDialog(QDialog):
    """Asked when placing a point: optional description and radius (default: none)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Punkt setzen")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        self._description_edit = description_edit("", for_point=True)
        description_label = QLabel("Beschreibung (auf der Karte)")
        description_label.setBuddy(self._description_edit)
        layout.addWidget(description_label)
        layout.addWidget(self._description_edit)
        layout.addSpacing(8)

        form = QFormLayout()
        self._radius_spin = radius_spinbox(0.0)
        form.addRow("Radius", self._radius_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        _add_confirm_shortcut(self)
        self._description_edit.setFocus()

    def description(self) -> str:
        return self._description_edit.toPlainText()

    def radius_m(self) -> float:
        """Radius circle in meters; 0 = none."""
        return self._radius_spin.value()


_PLACEHOLDER = "optional – mehrzeilig möglich, Strg+Enter bestätigt"
# Points additionally support $POS (replaced by the MGRS coordinate on the map)
_POINT_PLACEHOLDER = (
    f"optional, z. B. „Sammelstelle {POSITION_PLACEHOLDER}“\n"
    f"{POSITION_PLACEHOLDER} wird auf der Karte durch die MGRS-Koordinate ersetzt\n"
    "mehrzeilig möglich, Strg+Enter bestätigt"
)


def description_edit(text: str, for_point: bool = False) -> QPlainTextEdit:
    """Multi-line description field: Enter adds a line, Tab moves on (Ctrl+Enter confirms the dialog)."""
    edit = QPlainTextEdit(text)
    edit.setPlaceholderText(_POINT_PLACEHOLDER if for_point else _PLACEHOLDER)
    edit.setTabChangesFocus(True)
    line_height = edit.fontMetrics().lineSpacing()
    edit.setFixedHeight(line_height * 4 + 12)
    return edit


def _add_confirm_shortcut(dialog: QDialog) -> None:
    """Ctrl+Enter accepts the dialog — plain Enter inserts a line break in the description."""
    for keys in ("Ctrl+Return", "Ctrl+Enter"):
        QShortcut(QKeySequence(keys), dialog, activated=dialog.accept)


def radius_spinbox(value_m: float) -> QDoubleSpinBox:
    """Spin box for a point's radius circle in meters; 0 is shown as "kein Radius"."""
    spin = QDoubleSpinBox()
    spin.setDecimals(1)
    spin.setRange(0.0, 100_000.0)
    spin.setSingleStep(10.0)
    spin.setSuffix(" m")
    spin.setSpecialValueText("kein Radius")
    spin.setValue(value_m)
    spin.setToolTip("Maßstabsgetreuer Kreis um den Punkt, z. B. für Gefahren- oder Sperrbereiche")
    return spin


def _measurement_box(measurements: Measurements | None) -> QGroupBox | None:
    """Read-only box with length / perimeter / area; None if there is nothing to show."""
    if measurements is None:
        return None
    rows = []
    if measurements.length_m is not None:
        rows.append(("Länge", format_meters(measurements.length_m)))
    if measurements.perimeter_m is not None:
        rows.append(("Umfang", format_meters(measurements.perimeter_m)))
    if measurements.area_m2 is not None:
        rows.append(("Fläche", format_area(measurements.area_m2)))
    if not rows:
        return None

    box = QGroupBox("Messwerte")
    form = QFormLayout(box)
    for label, value in rows:
        value_label = QLabel(value)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(label, value_label)
    return box


def _color_button(parent, title: str, color: QColor) -> QgsColorButton:
    btn = QgsColorButton(parent, title)
    btn.setAllowOpacity(True)
    btn.setShowNoColor(False)
    btn.setColor(color)
    return btn
