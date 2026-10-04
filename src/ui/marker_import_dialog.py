"""Import-Dialog der Marker-Tabelle: Spalten einer CSV-/Excel-Datei den Marker-Feldern zuordnen."""

import os

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QBrush, QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..util.coordinates import parse_position
from ..util.table_import import FILE_FILTER, read_table, write_table
from .symbol_picker import SymbolCatalog, SymbolPickerDialog

_LAST_DIR_KEY = "thw_toolbox/marker_table/import_dir"
_INVALID_BG = QColor(255, 215, 215)
_NO_COLUMN = -1

# Übliche Spaltenüberschriften je Feld (klein geschrieben), für die Vorbelegung der Zuordnung
_HEADER_HINTS = {
    "position": ("position", "utmref", "mgrs", "koordinate", "koordinaten", "coordinates", "standort"),
    "lat": ("breite", "breitengrad", "lat", "latitude", "y"),
    "lon": ("länge", "laenge", "längengrad", "laengengrad", "lon", "lng", "long", "longitude", "x"),
    "label": ("beschriftung", "name", "label", "bezeichnung", "titel", "text"),
    "symbol": ("zeichen", "taktisches zeichen", "symbol", "icon", "typ"),
}

# Beispieltabelle zum Abspeichern und Ausfüllen; die Überschriften passen zu _HEADER_HINTS,
# die Zeilen zeigen die erlaubten Schreibweisen für Zeichen und Position
TEMPLATE_ROWS = [
    ["Zeichen", "Beschriftung", "Position"],
    ["GKW I", "GKW 1. Bergungsgruppe", "32U MC 68258 82070"],
    ["Bergungsgruppe", "B1", "32UMC6830082100"],
    ["THW Fahrzeuge/Anhänger Lichtmast", "Lichtmast Nord", "52.188180 8.535691"],
]


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _looks_like_data(cell: str) -> bool:
    try:
        parse_position(cell)
        return True
    except ValueError:
        pass
    try:
        float(cell.replace(",", "."))
        return True
    except ValueError:
        return False


class MarkerImportDialog(QDialog):
    """Ordnet die Spalten einer eingelesenen Tabelle zu und zeigt, was daraus wird.

    Die Position steht entweder in einer Spalte (UTMREF oder „Breite Länge“) oder
    verteilt auf zwei Spalten (Breite, Länge). Zeilen, denen danach noch Zeichen
    oder gültige Position fehlen, landen als Entwurf in der Marker-Tabelle.
    """

    def __init__(self, catalog: SymbolCatalog, path: str, rows: list[list[str]], parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._rows = rows
        self._default_path = None
        self._updating = False

        self.setWindowTitle("Marker importieren")
        self.resize(760, 560)
        layout = QVBoxLayout(self)

        file_label = QLabel(f"Datei: {os.path.basename(path)}")
        file_label.setToolTip(path)
        layout.addWidget(file_label)

        self.header_check = QCheckBox("Erste Zeile enthält Überschriften")
        self.header_check.setChecked(not any(_looks_like_data(cell) for cell in rows[0]))
        layout.addWidget(self.header_check)

        form = QFormLayout()
        self.position_combo = QComboBox()
        self.position_combo.setToolTip("Eine Spalte mit UTMREF (32U MC 68258 82070) oder „Breite Länge“")
        form.addRow("Position:", self.position_combo)
        self.lat_combo = QComboBox()
        self.lon_combo = QComboBox()
        pair = QHBoxLayout()
        pair.addWidget(QLabel("Breite"))
        pair.addWidget(self.lat_combo, 1)
        pair.addWidget(QLabel("Länge"))
        pair.addWidget(self.lon_combo, 1)
        form.addRow("oder getrennt:", pair)
        self.label_combo = QComboBox()
        form.addRow("Beschriftung:", self.label_combo)
        self.symbol_combo = QComboBox()
        self.symbol_combo.setToolTip("Spalte mit dem Namen des Zeichens, z. B. „GKW I“ oder „THW Fahrzeuge/GKW I“")
        form.addRow("Zeichen:", self.symbol_combo)
        self.default_button = QPushButton()
        self.default_button.setIconSize(QSize(24, 24))
        self.default_button.setToolTip("Für Zeilen ohne (erkanntes) Zeichen")
        self.default_button.clicked.connect(self._pick_default_symbol)
        form.addRow("Standard-Zeichen:", self.default_button)
        self.show_check = QCheckBox("Beschriftung auf der Karte anzeigen")
        form.addRow("", self.show_check)
        layout.addLayout(form)

        self.preview = QTableWidget(0, 3, self)
        self.preview.setHorizontalHeaderLabels(["Zeichen", "Beschriftung", "Position"])
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preview.setIconSize(QSize(24, 24))
        self.preview.setColumnWidth(0, 230)
        self.preview.setColumnWidth(1, 170)
        self.preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.preview, 1)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._import_button.setText("Importieren")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._combos = {
            "position": self.position_combo,
            "lat": self.lat_combo,
            "lon": self.lon_combo,
            "label": self.label_combo,
            "symbol": self.symbol_combo,
        }
        self._fill_combos(self._guess_columns())
        self._set_default_symbol(None)
        self.header_check.toggled.connect(self._on_header_toggled)
        for combo in self._combos.values():
            combo.currentIndexChanged.connect(self._update_preview)
        self.show_check.toggled.connect(self._update_preview)
        self._update_preview()

    # ------------------------------------------------------------------
    # Spalten-Zuordnung
    # ------------------------------------------------------------------

    def _has_header(self) -> bool:
        return self.header_check.isChecked()

    def _column_names(self) -> list[str]:
        names = [f"Spalte {_column_letter(i)}" for i in range(len(self._rows[0]))]
        if self._has_header():
            names = [f"{name}: {header}" if header else name for name, header in zip(names, self._rows[0])]
        return names

    def _guess_columns(self) -> dict[str, int]:
        """Zuordnung aus den Überschriften; ohne Treffer aus dem Inhalt der ersten Datenzeile."""
        guess = dict.fromkeys(self._combos, _NO_COLUMN)
        if self._has_header():
            headers = [header.lower() for header in self._rows[0]]
            for field, hints in _HEADER_HINTS.items():
                guess[field] = next((i for i, header in enumerate(headers) if header in hints), _NO_COLUMN)
        if guess["lat"] == _NO_COLUMN or guess["lon"] == _NO_COLUMN:
            guess["lat"] = guess["lon"] = _NO_COLUMN
        if guess["position"] != _NO_COLUMN:
            guess["lat"] = guess["lon"] = _NO_COLUMN
        elif guess["lat"] == _NO_COLUMN:
            data = self._data_rows()
            for i, cell in enumerate(data[0] if data else []):
                try:
                    parse_position(cell)
                except ValueError:
                    continue
                guess["position"] = i
                break
        return guess

    def _fill_combos(self, selection: dict[str, int]):
        names = self._column_names()
        self._updating = True
        try:
            for field, combo in self._combos.items():
                combo.clear()
                combo.addItem("— keine —", _NO_COLUMN)
                for i, name in enumerate(names):
                    combo.addItem(name, i)
                combo.setCurrentIndex(max(0, combo.findData(selection[field])))
        finally:
            self._updating = False

    def _on_header_toggled(self):
        self._fill_combos({field: combo.currentData() for field, combo in self._combos.items()})
        self._update_preview()

    def _pick_default_symbol(self):
        path = SymbolPickerDialog.pick(self._catalog, self, self._default_path)
        if path:
            self._set_default_symbol(path)
            self._update_preview()

    def _set_default_symbol(self, path):
        self._default_path = path
        symbol = self._catalog.by_path(path) if path else None
        self.default_button.setIcon(self._catalog.icon(path) if path else QIcon())
        self.default_button.setText(symbol.name if symbol else "Zeichen wählen …")

    # ------------------------------------------------------------------
    # Ergebnis
    # ------------------------------------------------------------------

    def _data_rows(self) -> list[list[str]]:
        return self._rows[1:] if self._has_header() else self._rows

    def _cell(self, row: list[str], field: str) -> str:
        column = self._combos[field].currentData()
        return row[column] if column is not None and column != _NO_COLUMN else ""

    def _position_text(self, row: list[str]) -> str:
        text = self._cell(row, "position")
        if text:
            return text
        lat, lon = self._cell(row, "lat"), self._cell(row, "lon")
        # Semikolon als Trenner, damit Dezimalkommas in den Zellen eindeutig bleiben
        return f"{lat}; {lon}" if lat or lon else ""

    def result_rows(self) -> list[dict]:
        """Zeilen im Format von ``MarkerTableDialog._append_row``; ``unknown`` = nicht erkannter Zeichenname."""
        result = []
        for row in self._data_rows():
            pos_text = self._position_text(row)
            point = None
            if pos_text:
                try:
                    point = parse_position(pos_text)
                except ValueError:
                    pass
            symbol_text = self._cell(row, "symbol")
            symbol = self._catalog.find(symbol_text) if symbol_text else None
            label = self._cell(row, "label")
            if not (pos_text or symbol_text or label):
                continue
            result.append(
                {
                    "path": symbol.path if symbol else self._default_path,
                    "label": label,
                    "show": self.show_check.isChecked(),
                    "point": point,
                    "pos_text": pos_text,
                    "unknown": symbol_text if symbol_text and symbol is None else "",
                }
            )
        return result

    def _update_preview(self):
        if self._updating:
            return
        rows = self.result_rows()
        self.preview.setRowCount(len(rows))
        ready = 0
        unknown = []
        for index, values in enumerate(rows):
            path, point = values["path"], values["point"]
            symbol = self._catalog.by_path(path) if path else None
            symbol_item = QTableWidgetItem(symbol.name if symbol else values["unknown"])
            if path:
                symbol_item.setIcon(self._catalog.icon(path))
            if values["unknown"]:
                symbol_item.setToolTip(f"Zeichen „{values['unknown']}“ nicht gefunden")
                if values["unknown"] not in unknown:
                    unknown.append(values["unknown"])
            if not path:
                symbol_item.setBackground(QBrush(_INVALID_BG))
            self.preview.setItem(index, 0, symbol_item)
            self.preview.setItem(index, 1, QTableWidgetItem(values["label"]))
            pos_item = QTableWidgetItem(values["pos_text"])
            if point is None:
                pos_item.setBackground(QBrush(_INVALID_BG))
            self.preview.setItem(index, 2, pos_item)
            ready += bool(path and point is not None)

        text = f"Zeilen: {len(rows)} – direkt als Marker angelegt: {ready}."
        if len(rows) > ready:
            text += (
                f" Ohne Zeichen oder gültige Position: {len(rows) - ready} –"
                " diese bleiben als Entwurf in der Tabelle und können dort ergänzt werden."
            )
        if unknown:
            shown = ", ".join(unknown[:5]) + (" …" if len(unknown) > 5 else "")
            text += f" Nicht erkannte Zeichen: {shown}."
        self.summary_label.setText(text)
        self._import_button.setEnabled(bool(rows))

    @staticmethod
    def save_template(parent=None) -> str | None:
        """Beispieltabelle zum Ausfüllen speichern; gibt den Pfad oder None bei Abbruch zurück."""
        settings = QgsSettings()
        start = os.path.join(settings.value(_LAST_DIR_KEY, os.path.expanduser("~")), "Marker-Vorlage.xlsx")
        path, selected = QFileDialog.getSaveFileName(
            parent, "Import-Vorlage speichern", start, "Excel (*.xlsx);;CSV (*.csv)"
        )
        if not path:
            return None
        if not path.lower().endswith((".xlsx", ".csv")):
            path += ".csv" if "csv" in selected else ".xlsx"
        try:
            write_table(path, TEMPLATE_ROWS)
        except ValueError as e:
            QMessageBox.warning(parent, "Import-Vorlage speichern", str(e))
            return None
        settings.setValue(_LAST_DIR_KEY, os.path.dirname(path))
        return path

    @classmethod
    def ask(cls, catalog: SymbolCatalog, parent=None) -> list[dict] | None:
        """Datei wählen, Spalten zuordnen; gibt die Zeilen oder None bei Abbruch zurück."""
        settings = QgsSettings()
        start = settings.value(_LAST_DIR_KEY, os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(parent, "Marker aus Datei importieren", start, FILE_FILTER)
        if not path:
            return None
        settings.setValue(_LAST_DIR_KEY, os.path.dirname(path))
        try:
            rows = read_table(path)
        except ValueError as e:
            QMessageBox.warning(parent, "Marker importieren", str(e))
            return None
        dialog = cls(catalog, path, rows, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result_rows()
