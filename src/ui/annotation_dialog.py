from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from ..layer.annotations import (
    KIND_LINE,
    KIND_POINT,
    KIND_POLYGON,
    KIND_TEXT,
    AnnotationEntry,
    Measurements,
    default_radius_fill,
)
from ..util.units import format_area, format_meters
from .config_dialog import line_width_spinbox

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
    new values are available via ``name()``, ``line_color()``, ``line_width()``, ``radius_m()`` and
    ``fill_color()``.
    """

    def __init__(
        self,
        entry: AnnotationEntry,
        default_fill_color: QColor,
        measurements: Measurements | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.deleted = False
        self._kind = entry.kind
        self.setWindowTitle(f"{entry.short_title} bearbeiten")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        # Label above the text field so the field can use the full dialog width
        self._name_edit = QLineEdit(entry.name)
        self._name_edit.setPlaceholderText("optional")
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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        delete_btn = buttons.addButton("Löschen", QDialogButtonBox.ButtonRole.DestructiveRole)
        delete_btn.clicked.connect(self._confirm_delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def name(self) -> str:
        return self._name_edit.text()

    def line_color(self) -> QColor:
        return self._line_color_btn.color()

    def radius_m(self) -> float | None:
        """Radius circle in meters (0 = none), or None for objects other than points."""
        return self._radius_spin.value() if self._radius_spin is not None else None

    def show_dimensions(self) -> bool | None:
        """Whether radius / edge lengths are shown on the map; None for texts."""
        return self._dimensions_check.isChecked() if self._dimensions_check is not None else None

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
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("optional")
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
        self._description_edit.setFocus()

    def description(self) -> str:
        return self._description_edit.text()

    def radius_m(self) -> float:
        """Radius circle in meters; 0 = none."""
        return self._radius_spin.value()


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
