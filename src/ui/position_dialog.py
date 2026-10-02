from qgis.core import QgsPointXY
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from ..util.coordinates import parse_position, to_mgrs


class PositionDialog(QDialog):
    """Popup zum Setzen der Marker-Position per Koordinate.

    Eingabe als „Breite Länge“ in Dezimalgrad (z. B. ``52.188180 8.535691``)
    oder als UTMREF. Der Ankerpunkt des Zeichens landet auf der Koordinate.
    """

    def __init__(self, parent=None, current: QgsPointXY | None = None):
        super().__init__(parent)
        self.setWindowTitle("Position setzen")
        self.setMinimumWidth(360)
        self.point: QgsPointXY | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Breite Länge (z. B. 52.188180 8.535691) oder UTMREF:"))

        self.edit = QLineEdit()
        layout.addWidget(self.edit)

        if current is not None:
            self.edit.setText(f"{current.y():.6f} {current.x():.6f}")
            self.edit.selectAll()
            try:
                layout.addWidget(QLabel(f"Aktuell: UTMREF {to_mgrs(current.y(), current.x())}"))
            except ValueError:
                pass

        hint = QLabel("Das Zeichen wird mit seinem Ankerpunkt auf die Koordinate gesetzt.")
        hint.setStyleSheet("QLabel { color: #666; }")
        layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("QLabel { color: #c0392b; }")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        try:
            self.point = parse_position(self.edit.text())
        except ValueError as e:
            self.error_label.setText(str(e))
            self.error_label.show()
            self.edit.setFocus()
            return
        self.accept()

    @classmethod
    def ask(cls, parent, current: QgsPointXY | None) -> QgsPointXY | None:
        """Zeigt das Popup; gibt den WGS84-Punkt (x = Länge, y = Breite) oder None bei Abbruch zurück."""
        dialog = cls(parent, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.point
