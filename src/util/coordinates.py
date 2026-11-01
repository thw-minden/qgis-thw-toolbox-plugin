"""Umrechnung WGS84 (Breite/Länge) ↔ UTMREF/MGRS und Parser für Koordinaten-Eingaben."""

import math
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

# Zone, Band und 100-km-Quadrat; danach Rechts- und Hochwert in beliebiger Schreibweise (siehe _split_digits)
_MGRS_RE = re.compile(r"^\s*(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(.*?)\s*$")
# Rechts-/Hochwert mit Nachkommastellen, z. B. „94254,3“ (immer 5 Stellen vor dem Komma)
_DECIMAL_VALUE_RE = re.compile(r"^\d{5}([.,]\d+)?$")

# Angezeigte UTMREF-Auflösung in Metern (Einstellung „UTMREF-Genauigkeit“): 10 m, 1 m oder 0,1 m
MGRS_RESOLUTIONS_M = (10.0, 1.0, 0.1)
_resolution_m = 1.0


def set_mgrs_resolution(resolution_m: float) -> None:
    """Auflösung, mit der ``to_mgrs`` ohne eigene Angabe schreibt (aus den Projekt-Einstellungen)."""
    global _resolution_m
    _resolution_m = resolution_m if resolution_m in MGRS_RESOLUTIONS_M else 1.0


def mgrs_resolution() -> float:
    return _resolution_m


def _transform(src: QgsCoordinateReferenceSystem, dst: QgsCoordinateReferenceSystem, point: QgsPointXY) -> QgsPointXY:
    return QgsCoordinateTransform(src, dst, QgsProject.instance()).transform(point)


def to_mgrs(lat: float, lon: float, resolution_m: float | None = None) -> str:
    """WGS84 → UTMREF, z. B. ``32U MC 94254 93123`` bei 1 m.

    ``resolution_m`` (Standard: Einstellung „UTMREF-Genauigkeit“) bestimmt die Stellen je Rechts- und
    Hochwert: 10 m → 4, 1 m → 5, 0,1 m → 6. Wie bei UTMREF üblich wird abgeschnitten, nicht gerundet.
    """
    resolution = resolution_m or _resolution_m
    if not -80.0 <= lat < 84.0:
        raise ValueError("UTMREF ist nur zwischen 80° S und 84° N definiert")
    zone = min(_utm_zone(lon), 60)
    utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(_utm_epsg(zone, lat))
    utm = _transform(WGS84, utm_crs, QgsPointXY(lon, lat))
    # Auf mm runden, bevor abgeschnitten wird: sonst wird aus einer eingegebenen
    # 83000 nach der Rückrechnung (82999,9999…) eine 82999
    easting, northing = round(utm.x(), 3), round(utm.y(), 3)
    square = _mgrs_100km(zone, easting, northing)
    digits = 5 - round(math.log10(resolution))
    # Kleiner Zuschlag gegen Gleitkomma-Reste (12344,9999… statt 12345)
    east = int(math.floor((easting % 100_000) / resolution + 1e-6))
    north = int(math.floor((northing % 100_000) / resolution + 1e-6))
    return f"{zone}{_latitude_band(lat)} {square} {east:0{digits}d} {north:0{digits}d}"


def from_mgrs(text: str) -> QgsPointXY:
    """UTMREF → WGS84-Punkt (x = Länge, y = Breite).

    Unabhängig von der eingestellten Anzeige-Genauigkeit: Leerzeichen sind beliebig, die Ziffern werden
    je zur Hälfte auf Ost- und Nordwert verteilt, 1 bis 6 Stellen je Wert (``32UMC9425493123``,
    ``32U MC 942 931``, ``32U MC 942543 931237`` für 0,1 m). Ebenso Werte mit Nachkommastellen,
    z. B. ``32U MC 94254,3 93123,7``. Kürzere Angaben bezeichnen wie üblich die Südwest-Ecke des Planquadrats.
    """
    match = _MGRS_RE.match(text.upper())
    if not match:
        raise ValueError("Format z. B. 32U MC 94254 93123")
    zone = int(match[1])
    band, col, row = match[2], match[3], match[4]
    if not 1 <= zone <= 60:
        raise ValueError("Zone muss zwischen 1 und 60 liegen")

    col_set = COL_SETS[(zone - 1) % 3]
    if col not in col_set:
        raise ValueError(f"100-km-Quadrat {col}{row} gibt es in Zone {zone} nicht")

    east, north = _split_digits(match[5])

    easting = (col_set.index(col) + 1) * 100_000 + east
    rowset = ROW_ODD if zone % 2 == 1 else ROW_EVEN
    row_northing = rowset.index(row) * 100_000
    while row_northing < _BAND_MIN_NORTHING[band]:
        row_northing += 2_000_000

    epsg = (32600 if band >= "N" else 32700) + zone
    utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
    return _transform(utm_crs, WGS84, QgsPointXY(easting, row_northing + north))


def _split_digits(text: str) -> tuple[float, float]:
    """Rechts- und Hochwert innerhalb des 100-km-Quadrats (Meter) aus dem Rest der Eingabe."""
    parts = text.split()
    if len(parts) == 2 and any(c in text for c in ".,"):
        if not all(_DECIMAL_VALUE_RE.match(p) for p in parts):
            raise ValueError("Mit Nachkommastellen: je 5 Stellen vor dem Komma, z. B. 94254,3 93123,7")
        east, north = (float(p.replace(",", ".")) for p in parts)
        return east, north
    if len(parts) == 2 and len(parts[0]) != len(parts[1]):
        raise ValueError("Ost- und Nordwert brauchen gleich viele Ziffern (je 1 bis 6)")
    digits = "".join(parts)
    if not digits.isdigit() and digits:
        raise ValueError("Format z. B. 32U MC 94254 93123")
    if len(digits) % 2 or len(digits) > 12:
        raise ValueError("Ost- und Nordwert brauchen gleich viele Ziffern (je 1 bis 6)")
    precision = len(digits) // 2
    if not precision:
        return 0.0, 0.0
    # 6 Stellen = Dezimeter: 10^(5-6) = 0,1
    factor = 10.0 ** (5 - precision)
    return int(digits[:precision]) * factor, int(digits[precision:]) * factor


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
