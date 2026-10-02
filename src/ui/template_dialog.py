"""Dialog zum Auswählen und Öffnen mitgelieferter Druckvorlagen (.qpt)."""

import os

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from qgis.utils import iface

from ..layout.print_template import (
    EINSTUFUNG_OFFEN,
    EINSTUFUNG_VSNFD,
    PrintInfo,
    load_print_template,
    ortsverband_names,
)


class TemplateDialog(QDialog):
    """Listet die .qpt-Dateien aus dem Plugin-Ordner `templates/` und öffnet sie im Designer.

    Ortsverband, Einheit und Bearbeiter werden in den Benutzereinstellungen
    gemerkt, der Einsatzname im Projekt.
    """

    def __init__(self, plugin_dir: str, parent=None):
        super().__init__(parent)
        self._plugin_dir = plugin_dir
        self._templates_dir = os.path.join(plugin_dir, "templates")
        self.setWindowTitle("THW Toolbox – Druckvorlagen")
        self.resize(480, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "Wählen Sie eine Druckvorlage und öffnen Sie sie im QGIS Layout-Designer. "
            "Die Vorlage wird als neues Layout im Projekt angelegt."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self._open_selected())
        layout.addWidget(self._list, 1)

        layout.addWidget(self._build_info_box(PrintInfo.load()))

        btn_row = QHBoxLayout()
        self._open_btn = QPushButton("Im Designer öffnen")
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self._open_selected)
        btn_row.addStretch(1)
        btn_row.addWidget(self._open_btn)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _build_info_box(self, info: PrintInfo) -> QGroupBox:
        box = QGroupBox("Angaben im Kartenrand")
        form = QFormLayout(box)

        self._ov_edit = QLineEdit(info.ortsverband)
        self._ov_edit.setPlaceholderText("Name eingeben, z. B. Aachen")
        completer = QCompleter(ortsverband_names(self._plugin_dir), self._ov_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._ov_edit.setCompleter(completer)
        form.addRow("Ortsverband:", self._ov_edit)

        self._einheit_edit = QLineEdit(info.einheit)
        form.addRow("Einheit:", self._einheit_edit)

        self._einheit_kurz_edit = QLineEdit(info.einheit_kurz)
        self._einheit_kurz_edit.setToolTip("Kurzform für den Bildnachweis, z. B. „TrUL“")
        form.addRow("Einheit (Kürzel):", self._einheit_kurz_edit)

        self._bearbeiter_edit = QLineEdit(info.bearbeiter)
        form.addRow("Bearbeiter:", self._bearbeiter_edit)

        self._einsatz_edit = QLineEdit(info.einsatz)
        self._einsatz_edit.setToolTip("Wird im Projekt gespeichert")
        form.addRow("Einsatz / Titel:", self._einsatz_edit)

        self._einstufung_combo = QComboBox()
        self._einstufung_combo.addItems([EINSTUFUNG_OFFEN, EINSTUFUNG_VSNFD])
        self._einstufung_combo.setCurrentText(info.einstufung)
        form.addRow("Einstufung:", self._einstufung_combo)

        self._legend_check = QCheckBox("Legende: jedes Zeichen einmal, einheitliche Größe")
        self._legend_check.setChecked(info.uniform_legend)
        self._legend_check.setToolTip(
            "Die Legende wird beim Öffnen einmalig aufgebaut. Später hinzugefügte Zeichen "
            "erscheinen erst, wenn die Vorlage erneut geöffnet wird."
        )
        form.addRow(self._legend_check)

        return box

    def _current_info(self) -> PrintInfo:
        return PrintInfo(
            ortsverband=self._ov_edit.text().strip(),
            einheit=self._einheit_edit.text().strip(),
            einheit_kurz=self._einheit_kurz_edit.text().strip(),
            bearbeiter=self._bearbeiter_edit.text().strip(),
            einsatz=self._einsatz_edit.text().strip(),
            einstufung=self._einstufung_combo.currentText(),
            uniform_legend=self._legend_check.isChecked(),
        )

    def _populate(self) -> None:
        self._list.clear()
        if not os.path.isdir(self._templates_dir):
            self._show_empty("Kein `templates/`-Ordner im Plugin gefunden.")
            return

        files = sorted(f for f in os.listdir(self._templates_dir) if f.lower().endswith(".qpt"))
        if not files:
            self._show_empty("Keine Vorlagen (.qpt) im Plugin-Ordner `templates/` vorhanden.")
            return

        for name in files:
            item = QListWidgetItem(os.path.splitext(name)[0])
            item.setData(Qt.ItemDataRole.UserRole, os.path.join(self._templates_dir, name))
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._open_btn.setEnabled(True)

    def _show_empty(self, message: str) -> None:
        item = QListWidgetItem(message)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(item)
        self._open_btn.setEnabled(False)

    def _open_selected(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return

        info = self._current_info()
        if not info.ortsverband:
            QMessageBox.warning(self, "Ortsverband fehlt", "Bitte den Ortsverband angeben.")
            self._ov_edit.setFocus()
            return
        info.save()

        try:
            layout = load_print_template(path, info, os.path.join(self._templates_dir, "assets"))
        except ValueError as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        base_name = os.path.splitext(os.path.basename(path))[0]
        layout.setName(self._unique_layout_name(base_name))
        QgsProject.instance().layoutManager().addLayout(layout)
        iface.openLayoutDesigner(layout)
        self.accept()

    @staticmethod
    def _unique_layout_name(base: str) -> str:
        manager = QgsProject.instance().layoutManager()
        existing = {lay.name() for lay in manager.layouts()}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"
