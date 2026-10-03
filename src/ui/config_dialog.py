from qgis.gui import QgsColorButton
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
)

# Settings stores label_font_size_mm / label_buffer_size_mm as floats but the
# UI exposes them as integers (one decimal precision). We multiply/divide by 10.
_MM_TO_INT = 10


class ConfigDialog(QDialog):
    """Plugin settings dialog: defaults for new icons, label appearance and annotation colors.

    Reads current values from `settings` on construction and writes them
    back when the user clicks OK. Caller is responsible for persisting
    settings (save_settings) and refreshing the renderer.

    Use `exec_and_apply()` for the typical flow: returns True if the user
    accepted and settings were applied; False if cancelled.
    """

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("THW Toolbox Einstellungen")

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_default_icon_group())
        layout.addWidget(self._build_label_group())
        layout.addWidget(self._build_annotation_group())

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(button_box)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

    def exec_and_apply(self) -> bool:
        """Show modal; on Accept, write widget values back to settings. Returns True on Accept."""
        if self.exec() != QDialog.DialogCode.Accepted:
            return False
        self._apply_to_settings()
        return True

    def _build_default_icon_group(self) -> QGroupBox:
        box = QGroupBox("Einstellungen für neue Zeichen")
        form = QFormLayout()

        self._cb_scale = QCheckBox()
        self._cb_scale.setChecked(self._settings.new_icon_scaling_with_map)
        form.addRow("Neue Zeichen mit Karte skalieren", self._cb_scale)

        self._cb_fixed_size = QCheckBox()
        self._cb_fixed_size.setChecked(self._settings.new_icon_fixed_size)
        form.addRow("Neue Zeichen mit fixer Standardgröße", self._cb_fixed_size)

        self._spin_icon_size = QSpinBox()
        self._spin_icon_size.setMinimum(10)
        self._spin_icon_size.setMaximum(2000)
        self._spin_icon_size.setSingleStep(1)
        self._spin_icon_size.setValue(self._settings.new_icon_size)
        form.addRow("Fixe Standardgröße für neue Zeichen", self._spin_icon_size)

        # Spinbox is only relevant when fixed-size is enabled
        self._cb_fixed_size.stateChanged.connect(lambda checked: self._spin_icon_size.setEnabled(bool(checked)))
        self._spin_icon_size.setEnabled(self._cb_fixed_size.isChecked())

        self._dropdown_crs = QComboBox()
        self._dropdown_crs.addItems(["MGRS", "UTM"])
        idx = self._dropdown_crs.findText(self._settings.new_icon_crs)
        if idx >= 0:
            self._dropdown_crs.setCurrentIndex(idx)
        # Row intentionally not added — coordinate-system selector is currently unused

        box.setLayout(form)
        return box

    def _build_label_group(self) -> QGroupBox:
        box = QGroupBox("Label Settings")
        form = QFormLayout()

        self._cb_label_enable = QCheckBox()
        self._cb_label_enable.setChecked(self._settings.label_enable)
        form.addRow("Aktivierte Labels anzeigen", self._cb_label_enable)

        self._sb_label_font_size = QSpinBox()
        self._sb_label_font_size.setMinimum(1)
        self._sb_label_font_size.setMaximum(100)
        self._sb_label_font_size.setSingleStep(1)
        self._sb_label_font_size.setValue(int(self._settings.label_font_size_mm * _MM_TO_INT))
        form.addRow("Label Schriftgröße", self._sb_label_font_size)

        self._sb_label_buffer_size = QSpinBox()
        self._sb_label_buffer_size.setMinimum(1)
        self._sb_label_buffer_size.setMaximum(50)
        self._sb_label_buffer_size.setSingleStep(1)
        self._sb_label_buffer_size.setValue(int(self._settings.label_buffer_size_mm * _MM_TO_INT))
        form.addRow("Label Rahmendicke", self._sb_label_buffer_size)

        box.setLayout(form)
        return box

    def _build_annotation_group(self) -> QGroupBox:
        box = QGroupBox("Annotationen")
        form = QFormLayout()

        self._btn_annotation_line_color = self._color_button(
            "Linienfarbe für Annotationen", self._settings.annotation_line_color
        )
        form.addRow("Linienfarbe (Linien, Umrisse, Punkte)", self._btn_annotation_line_color)

        self._btn_annotation_fill_color = self._color_button(
            "Füllfarbe für Annotationen", self._settings.annotation_fill_color
        )
        form.addRow("Füllfarbe (gefüllte Polygone)", self._btn_annotation_fill_color)

        self._sb_annotation_line_width = line_width_spinbox(self._settings.annotation_line_width_mm)
        form.addRow("Linienbreite (Linien, Umrisse)", self._sb_annotation_line_width)

        self._cb_mgrs_resolution = QComboBox()
        for dm, label in zip(self._settings.ANNOTATION_MGRS_RESOLUTIONS_DM, ("0,1 m", "1 m", "10 m", "100 m")):
            self._cb_mgrs_resolution.addItem(label, dm)
        self._cb_mgrs_resolution.setCurrentIndex(
            max(0, self._cb_mgrs_resolution.findData(self._settings.annotation_mgrs_resolution_dm))
        )
        self._cb_mgrs_resolution.setToolTip(
            "Genauigkeit von MGRS-Koordinaten: $POS in Punktbeschreibungen, Koordinaten-Tabellen und Marker-Details"
        )
        form.addRow("MGRS-Auflösung", self._cb_mgrs_resolution)

        box.setLayout(form)
        return box

    def _color_button(self, title: str, color: str) -> QgsColorButton:
        btn = QgsColorButton(self, title)
        btn.setAllowOpacity(True)
        btn.setShowNoColor(False)
        btn.setColor(QColor(color))
        return btn

    def _apply_to_settings(self) -> None:
        self._settings.new_icon_scaling_with_map = self._cb_scale.isChecked()
        self._settings.new_icon_fixed_size = self._cb_fixed_size.isChecked()
        self._settings.new_icon_size = self._spin_icon_size.value()
        self._settings.new_icon_crs = self._dropdown_crs.currentText()

        self._settings.label_enable = self._cb_label_enable.isChecked()
        self._settings.label_font_size_mm = self._sb_label_font_size.value() / _MM_TO_INT
        self._settings.label_buffer_size_mm = self._sb_label_buffer_size.value() / _MM_TO_INT

        self._settings.annotation_line_color = self._btn_annotation_line_color.color()
        self._settings.annotation_fill_color = self._btn_annotation_fill_color.color()
        self._settings.annotation_line_width_mm = self._sb_annotation_line_width.value()
        self._settings.annotation_mgrs_resolution_dm = self._cb_mgrs_resolution.currentData()


def line_width_spinbox(value_mm: float) -> QDoubleSpinBox:
    """Spin box for annotation line widths in mm (shared by settings and edit dialog)."""
    spin = QDoubleSpinBox()
    spin.setDecimals(1)
    spin.setRange(0.1, 10.0)
    spin.setSingleStep(0.1)
    spin.setSuffix(" mm")
    spin.setValue(value_mm)
    return spin
