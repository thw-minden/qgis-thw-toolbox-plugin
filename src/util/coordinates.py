"""Umrechnung WGS84 (Breite/Länge) ↔ UTMREF/MGRS und Parser für Koordinaten-Eingaben."""

import re

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject

from ..layout.mgrs_grid import COL_SETS, ROW_EVEN, ROW_ODD, _latitude_band, _mgrs_100km, _utm_epsg, _utm_zone

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# Kleinster UTM-Nordwert je Breitenband (MGRS-Standard). Damit wird der alle
# 2000 km wiederkehrende Zeilenbuchstabe des 100-km-Quadrats eindeutig.
_BAND_MIN_NORTHING = {
    "C": 1_100_000,
    "D": 2_000_000,
    "E": 2_800_000,
    "F": 3_700_000,
    "G": 4_600_000,
    "H": 5_500_000,
    "J": 6_400_000,
    "K": 7_300_000,
    "L": 8_200_000,
    "M": 9_100_000,
    "N": 0,
    "P": 800_000,
    "Q": 1_700_000,
    "R": 2_600_000,
    "S": 3_500_000,
    "T": 4_400_000,
    "U": 5_300_000,
    "V": 6_200_000,
    "W": 7_000_000,
    "X": 7_900_000,
}

_MGRS_RE = re.compile(r"^(\d{1,2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])(\d*)$")


def _transform(src: QgsCoordinateReferenceSystem, dst: QgsCoordinateReferenceSystem, point: QgsPointXY) -> QgsPointXY:
    return QgsCoordinateTransform(src, dst, QgsProject.instance()).transform(point)


def to_mgrs(lat: float, lon: float) -> str:
    """WGS84 → UTMREF mit 1 m Auflösung, z. B. ``32U MC 94254 93123``."""
    if not -80.0 <= lat < 84.0:
        raise ValueError("UTMREF ist nur zwischen 80° S und 84° N definiert")
    zone = min(_utm_zone(lon), 60)
    utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(_utm_epsg(zone, lat))
    utm = _transform(WGS84, utm_crs, QgsPointXY(lon, lat))
    # Auf mm runden, bevor abgeschnitten wird: sonst wird aus einer eingegebenen
    # 83000 nach der Rückrechnung (82999,9999…) eine 82999
    easting, northing = round(utm.x(), 3), round(utm.y(), 3)
    square = _mgrs_100km(zone, easting, northing)
    return f"{zone}{_latitude_band(lat)} {square} {int(easting) % 100_000:05d} {int(northing) % 100_000:05d}"


def from_mgrs(text: str) -> QgsPointXY:
    """UTMREF → WGS84-Punkt (x = Länge, y = Breite).

    Leerzeichen sind beliebig, die Ziffern werden je zur Hälfte auf Ost- und
    Nordwert verteilt (``32UMC9425493123``, ``32U MC 942 931`` usw.).
    Kürzere Angaben bezeichnen wie üblich die Südwest-Ecke des Planquadrats.
    """
    match = _MGRS_RE.match("".join(text.upper().split()))
    if not match:
        raise ValueError("Format z. B. 32U MC 94254 93123")
    zone = int(match[1])
    band, col, row, digits = match[2], match[3], match[4], match[5]
    if not 1 <= zone <= 60:
        raise ValueError("Zone muss zwischen 1 und 60 liegen")
    if len(digits) % 2 or len(digits) > 10:
        raise ValueError("Ost- und Nordwert brauchen gleich viele Ziffern (max. je 5)")

    col_set = COL_SETS[(zone - 1) % 3]
    if col not in col_set:
        raise ValueError(f"100-km-Quadrat {col}{row} gibt es in Zone {zone} nicht")

    precision = len(digits) // 2
    factor = 10 ** (5 - precision)
    east = int(digits[:precision]) * factor if precision else 0
    north = int(digits[precision:]) * factor if precision else 0

    easting = (col_set.index(col) + 1) * 100_000 + east
    rowset = ROW_ODD if zone % 2 == 1 else ROW_EVEN
    row_northing = rowset.index(row) * 100_000
    while row_northing < _BAND_MIN_NORTHING[band]:
        row_northing += 2_000_000

    epsg = (32600 if band >= "N" else 32700) + zone
    utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
    return _transform(utm_crs, WGS84, QgsPointXY(easting, row_northing + north))


def parse_decimal_pair(text: str) -> QgsPointXY | None:
    """Liest „Breite Länge“ in Dezimalgrad, z. B. ``52.188180 8.535691``.

    Trenner: Leerzeichen, Komma+Leerzeichen oder Semikolon; Dezimalkomma ist erlaubt
    (``52,188180 8,535691``). Gibt None zurück, wenn der Text kein solches Paar ist.
    """
    value = text.strip()
    if ";" in value:
        parts = value.split(";")
    else:
        parts = [p.strip(",") for p in value.split()]
        parts = [p for p in parts if p]
        if len(parts) == 1 and parts[0].count(",") == 1:
            parts = parts[0].split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = (float(p.strip().replace(",", ".")) for p in parts)
    except ValueError:
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return QgsPointXY(lon, lat)


def parse_position(text: str) -> QgsPointXY:
    """Eingabe „Breite Länge“ (Dezimalgrad) oder UTMREF → WGS84-Punkt (x = Länge, y = Breite)."""
    if not text.strip():
        raise ValueError("Bitte eine Koordinate eingeben")
    point = parse_decimal_pair(text)
    if point is not None:
        return point
    try:
        return from_mgrs(text)
    except ValueError as e:
        raise ValueError(f"Weder „Breite Länge“ (z. B. 52.188180 8.535691) noch UTMREF ({e})") from None
