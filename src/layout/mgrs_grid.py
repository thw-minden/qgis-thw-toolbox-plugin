"""Buchstaben und Hilfsfunktionen des Meldegitters (MGRS/UTMREF): Breitenband, UTM-Zone, 100-km-Quadrat.

Genutzt von der Umrechnung in ``util.coordinates`` und den Ausdrücken der Objektplanung. Das UTMREF-Gitter
der Karten zeichnen die Druckvorlagen selbst; einen eigenen Gitter-Layer gibt es nicht mehr.
"""

BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"
COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
ROW_ODD = "ABCDEFGHJKLMNPQRSTUV"
ROW_EVEN = "FGHJKLMNPQRSTUVABCDE"


def _latitude_band(lat: float) -> str:
    if lat >= 84:
        return "X"
    if lat < -80:
        return "C"
    return BAND_LETTERS[min(int((lat + 80) // 8), 19)]


def _utm_zone(lon: float) -> int:
    return int((lon + 180) // 6) + 1


def _utm_epsg(zone: int, lat: float) -> int:
    return (32600 if lat >= 0 else 32700) + zone


def _mgrs_100km(zone: int, easting: float, northing: float) -> str:
    col_idx = max(0, min(7, int(easting // 100_000) - 1))
    col = COL_SETS[(zone - 1) % 3][col_idx]
    rowset = ROW_ODD if zone % 2 == 1 else ROW_EVEN
    row = rowset[int((northing % 2_000_000) // 100_000)]
    return col + row
