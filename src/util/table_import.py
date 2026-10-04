"""Tabellen aus CSV-/Excel-Dateien als Textzeilen einlesen (für den Import in die Marker-Tabelle)."""

import csv
import io
import os

from ..logging_utils import get_logger

logger = get_logger(__name__)

CSV_EXTENSIONS = (".csv", ".tsv", ".txt")
SHEET_EXTENSIONS = (".xlsx", ".ods")
FILE_FILTER = "Tabellen (*.csv *.tsv *.txt *.xlsx *.ods);;CSV (*.csv *.tsv *.txt);;Excel / OpenDocument (*.xlsx *.ods)"

# Reihenfolge = Vorrang: Excel exportiert in Deutschland mit Semikolon, das Komma
# ist dort Dezimaltrenner und darf deshalb erst zuletzt als Trenner gelten
_DELIMITERS = (";", "\t", "|", ",")
_SNIFF_LINES = 20


def read_table(path: str) -> list[list[str]]:
    """Liest die Datei als Liste von Zeilen mit Textzellen; leere Zeilen entfallen.

    Wirft ValueError mit einer Meldung für den Benutzer, wenn die Datei nicht lesbar ist.
    """
    extension = os.path.splitext(path)[1].lower()
    if extension in SHEET_EXTENSIONS:
        rows = _read_sheet(path)
    elif extension in CSV_EXTENSIONS:
        rows = _read_csv(path)
    else:
        raise ValueError(f"Dateityp „{extension}“ wird nicht unterstützt (CSV, XLSX oder ODS).")

    rows = [[cell.strip() for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        raise ValueError("Die Datei enthält keine Daten.")
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def write_table(path: str, rows: list[list[str]]) -> None:
    """Schreibt die Zeilen (erste = Überschriften) als CSV oder XLSX, z. B. als Import-Vorlage.

    Wirft ValueError mit einer Meldung für den Benutzer, wenn die Datei nicht geschrieben werden kann.
    """
    try:
        if path.lower().endswith(".xlsx"):
            _write_xlsx(path, rows)
        else:
            # Semikolon + BOM: so öffnet Excel die Datei direkt mit Spalten und Umlauten
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f, delimiter=";").writerows(rows)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Die Datei konnte nicht gespeichert werden: {e}") from None


def _write_xlsx(path: str, rows: list[list[str]]) -> None:
    from osgeo import ogr

    if os.path.exists(path):
        os.remove(path)
    driver = ogr.GetDriverByName("XLSX")
    dataset = driver.CreateDataSource(path) if driver is not None else None
    if dataset is None:
        raise OSError("Excel-Dateien können mit dieser QGIS-Installation nicht geschrieben werden")
    layer = dataset.CreateLayer("Marker")
    for header in rows[0]:
        layer.CreateField(ogr.FieldDefn(header, ogr.OFTString))
    for row in rows[1:]:
        feature = ogr.Feature(layer.GetLayerDefn())
        for index, cell in enumerate(row):
            feature.SetField(index, cell)
        layer.CreateFeature(feature)
    dataset.FlushCache()
    dataset = None  # schließt und schreibt die Datei


def _read_csv(path: str) -> list[list[str]]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError(f"Die Datei konnte nicht gelesen werden: {e}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel speichert „CSV (Trennzeichen-getrennt)“ unter Windows in der ANSI-Codepage
        text = raw.decode("cp1252", errors="replace")
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=_guess_delimiter(text)))


def _guess_delimiter(text: str) -> str:
    """Erster Trenner, der in den ersten Zeilen durchgehend dieselbe Spaltenzahl (> 1) ergibt."""
    sample = "\n".join(text.splitlines()[:_SNIFF_LINES])
    best, best_width = ",", 1
    for delimiter in _DELIMITERS:
        widths = {len(row) for row in csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter) if row}
        if len(widths) == 1 and min(widths) > 1:
            return delimiter
        if widths and max(widths) > best_width:
            best, best_width = delimiter, max(widths)
    return best


def _read_sheet(path: str) -> list[list[str]]:
    """Erstes nicht leeres Tabellenblatt einer XLSX-/ODS-Datei über GDAL (in QGIS immer vorhanden)."""
    from osgeo import gdal, ogr

    prefix = "OGR_XLSX" if path.lower().endswith(".xlsx") else "OGR_ODS"
    # Kopfzeile und Zelltypen nicht von GDAL raten lassen: alles kommt als Text,
    # die Kopfzeile bestimmt der Import-Dialog
    options = {f"{prefix}_HEADERS": "DISABLE", f"{prefix}_FIELD_TYPES": "STRING"}
    previous = {key: gdal.GetConfigOption(key) for key in options}
    for key, value in options.items():
        gdal.SetConfigOption(key, value)
    try:
        dataset = ogr.Open(path)
        if dataset is None:
            raise ValueError("Die Datei konnte nicht als Tabelle geöffnet werden.")
        for index in range(dataset.GetLayerCount()):
            layer = dataset.GetLayerByIndex(index)
            count = layer.GetLayerDefn().GetFieldCount()
            rows = [
                [feature.GetFieldAsString(i) if feature.IsFieldSetAndNotNull(i) else "" for i in range(count)]
                for feature in layer
            ]
            if any(any(cell.strip() for cell in row) for row in rows):
                return rows
        return []
    finally:
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)
