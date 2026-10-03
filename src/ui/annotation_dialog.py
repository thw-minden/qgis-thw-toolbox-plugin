from qgis.gui import QgsColorButton
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from ..layer.annotations import KIND_LINE, KIND_POINT, KIND_POLYGON, KIND_TEXT, AnnotationEntry
from .config_dialog import line_width_spinbox

_TEXT_LABELS = {
    KIND_TEXT: "Text (auf der Karte)",
}


class AnnotationEditDialog(QDialog):
    """Edit text and colors of one annotation object, or delete it.

    After ``exec()`` returns Accepted, either ``deleted`` is True or the
    new values are available via ``name()``, ``line_color()``, ``line_width()`` and ``fill_color()``.
    """

    def __init__(self, entry: AnnotationEntry, default_fill_color: QColor, parent=None):
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

        self._fill_check = None
        self._fill_color_btn = None
        if entry.kind == KIND_POLYGON:
            self._fill_check = QCheckBox("Fläche füllen")
            self._fill_check.setChecked(entry.fill_color is not None)
            self._fill_color_btn = _color_button(self, "Füllfarbe", entry.fill_color or default_fill_color)
            self._fill_color_btn.setEnabled(self._fill_check.isChecked())
            self._fill_check.toggled.connect(self._fill_color_btn.setEnabled)
            form.addRow("", self._fill_check)
            form.addRow("Füllfarbe", self._fill_color_btn)

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

    def line_width(self) -> float | None:
        """Line width in mm, or None for objects without a line (points, texts)."""
        return self._line_width_spin.value() if self._line_width_spin is not None else None

    def fill_color(self) -> QColor | None:
        if self._fill_check is None or not self._fill_check.isChecked():
            return None
        return self._fill_color_btn.color()

    def _confirm_delete(self):
        answer = QMessageBox.question(
            self,
            "Annotation löschen",
            "Soll dieses Objekt wirklich von der Karte entfernt werden?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.accept()


def _color_button(parent, title: str, color: QColor) -> QgsColorButton:
    btn = QgsColorButton(parent, title)
    btn.setAllowOpacity(True)
    btn.setShowNoColor(False)
    btn.setColor(color)
    return btn
