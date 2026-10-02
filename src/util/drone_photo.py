"""Drohnenfotos auswerten: EXIF- und DJI-XMP-Metadaten lesen, Sichtstrahl durch einen Bildpunkt berechnen.

Bewusst ohne QGIS und ohne Fremdbibliotheken (kein exiftool, kein Pillow), damit
es auf jedem Einsatzrechner läuft und sich ohne QGIS testen lässt. Koordinaten
im lokalen System: x = Ost, y = Nord, z = oben (Meter, Kamera im Ursprung).
"""

import math
import re
import struct
from dataclasses import dataclass

# Diagonale des Kleinbildformats (36 × 24 mm), Bezug der KB-äquivalenten Brennweite
KB_DIAGONAL_MM = math.hypot(36.0, 24.0)
EARTH_RADIUS_M = 6_371_000.0

_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_ATTR_RE = re.compile(r'([A-Za-z][\w.-]*):([A-Za-z][\w.-]*)\s*=\s*"([^"]*)"')
_XMP_ELEM_RE = re.compile(r"<([A-Za-z][\w.-]*):([A-Za-z][\w.-]*)>([^<]*)</\1:\2>")

# EXIF-Tags
_TAG_MAKE, _TAG_MODEL = 0x010F, 0x0110
_TAG_EXIF_IFD, _TAG_GPS_IFD = 0x8769, 0x8825
_TAG_FOCAL_LENGTH, _TAG_FOCAL_35MM = 0x920A, 0xA405
_TAG_PIXEL_X, _TAG_PIXEL_Y = 0xA002, 0xA003
_TAG_FOCAL_PLANE_XRES, _TAG_FOCAL_PLANE_UNIT = 0xA20E, 0xA210
_FOCAL_PLANE_UNIT_MM = {2: 25.4, 3: 10.0, 4: 1.0, 5: 0.001}

# Größe je EXIF-Datentyp in Byte
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


@dataclass
class PhotoMetadata:
    """Für die Zielbestimmung relevante Aufnahmedaten; fehlende Werte sind None."""

    path: str
    width: int = 0
    height: int = 0
    make: str = ""
    model: str = ""
    lat: float | None = None
    lon: float | None = None
    abs_alt: float | None = None  # m, Bezug je nach Modell NN oder Ellipsoid
    rel_alt: float | None = None  # m über dem Startpunkt
    gimbal_yaw: float | None = None  # Grad, 0 = Nord, im Uhrzeigersinn
    gimbal_pitch: float | None = None  # Grad, 0 = Horizont, -90 = senkrecht nach unten
    gimbal_roll: float = 0.0
    focal_35mm: float | None = None  # KB-äquivalente Brennweite in mm
    center_x: float | None = None  # Bildhauptpunkt in Pixeln (sonst Bildmitte)
    center_y: float | None = None
    lrf_lat: float | None = None  # Ziel des Laser-Entfernungsmessers (Bildmitte)
    lrf_lon: float | None = None
    lrf_distance: float | None = None  # m, schräg
    lrf_abs_alt: float | None = None

    @property
    def camera(self) -> str:
        return " ".join(part for part in (self.make, self.model) if part)

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def has_lrf(self) -> bool:
        return (
            self.lrf_lat is not None
            and self.lrf_lon is not None
            and self.lrf_distance is not None
            and self.lrf_distance > 0
            and (self.lrf_lat, self.lrf_lon) != (0.0, 0.0)
        )

    def principal_point(self) -> tuple[float, float]:
        cx, cy = self.width / 2.0, self.height / 2.0
        # Kalibrierte Werte nur übernehmen, wenn sie zur Bildgröße passen
        if self.center_x is not None and self.center_y is not None:
            if abs(self.center_x - cx) < 0.2 * self.width and abs(self.center_y - cy) < 0.2 * self.height:
                return self.center_x, self.center_y
        return cx, cy

    def focal_length_px(self, focal_35mm: float | None = None) -> float | None:
        focal = focal_35mm if focal_35mm is not None else self.focal_35mm
        if not focal or not self.width or not self.height:
            return None
        return focal * math.hypot(self.width, self.height) / KB_DIAGONAL_MM


# ----------------------------------------------------------------------
# Metadaten lesen
# ----------------------------------------------------------------------


def read_photo_metadata(path: str) -> PhotoMetadata:
    """Liest EXIF und XMP eines JPEG-Fotos. Wirft ValueError bei Nicht-JPEG oder defekter Datei."""
    exif_payload = None
    xmp_chunks = []
    width = height = 0
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise ValueError("Keine JPEG-Datei")
        while True:
            byte = f.read(1)
            if not byte:
                break
            if byte != b"\xff":
                continue
            marker = f.read(1)
            while marker == b"\xff":  # Füllbytes
                marker = f.read(1)
            if not marker:
                break
            code = marker[0]
            if code == 0xD8 or code == 0x01 or 0xD0 <= code <= 0xD7:
                continue
            if code in (0xD9, 0xDA):  # Bildende / Bilddaten beginnen – keine Metadaten mehr
                break
            raw_len = f.read(2)
            if len(raw_len) < 2:
                break
            length = struct.unpack(">H", raw_len)[0] - 2
            if length < 0:
                break
            if code == 0xE1:
                payload = f.read(length)
                if payload.startswith(b"Exif\x00\x00") and exif_payload is None:
                    exif_payload = payload[6:]
                elif payload.startswith(_XMP_HEADER):
                    xmp_chunks.append(payload[len(_XMP_HEADER) :])
            elif 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                # Start of Frame: Präzision (1), Höhe (2), Breite (2)
                sof = f.read(length)
                if len(sof) >= 5:
                    height, width = struct.unpack(">HH", sof[1:5])
            else:
                f.seek(length, 1)

    meta = PhotoMetadata(path=path, width=width, height=height)
    if exif_payload:
        _apply_exif(meta, exif_payload)
    if xmp_chunks:
        _apply_xmp(meta, parse_xmp(b"".join(xmp_chunks)))
    return meta


def parse_xmp(data: bytes) -> dict[str, str]:
    """XMP als flaches Dict ``{"präfix:Name": "Wert"}`` (Attribut- und Element-Schreibweise)."""
    text = data.decode("utf-8", errors="replace")
    values = {}
    for prefix, name, value in _XMP_ATTR_RE.findall(text):
        if prefix != "xmlns":
            values.setdefault(f"{prefix}:{name}", value.strip())
    for prefix, name, value in _XMP_ELEM_RE.findall(text):
        values.setdefault(f"{prefix}:{name}", value.strip())
    return values


def _xmp_float(xmp: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = xmp.get(key)
        if value is None:
            continue
        try:
            number = float(value.replace(",", "."))
        except ValueError:
            continue
        if math.isfinite(number):
            return number
    return None


def _apply_xmp(meta: PhotoMetadata, xmp: dict[str, str]) -> None:
    # DJI schreibt die Position genauer als EXIF; ältere Firmware mit Tippfehler „Longtitude“
    lat = _xmp_float(xmp, "drone-dji:GpsLatitude", "drone-dji:Latitude")
    lon = _xmp_float(xmp, "drone-dji:GpsLongitude", "drone-dji:GpsLongtitude", "drone-dji:Longitude")
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        meta.lat, meta.lon = lat, lon

    abs_alt = _xmp_float(xmp, "drone-dji:AbsoluteAltitude")
    if abs_alt is not None:
        meta.abs_alt = abs_alt
    meta.rel_alt = _xmp_float(xmp, "drone-dji:RelativeAltitude")

    meta.gimbal_yaw = _xmp_float(xmp, "drone-dji:GimbalYawDegree")
    meta.gimbal_pitch = _xmp_float(xmp, "drone-dji:GimbalPitchDegree")
    meta.gimbal_roll = _xmp_float(xmp, "drone-dji:GimbalRollDegree") or 0.0
    if meta.gimbal_yaw is None:
        meta.gimbal_yaw = _xmp_float(xmp, "drone-dji:FlightYawDegree")

    # Kalibrierte Brennweite (Pixel) der Enterprise-Modelle ist genauer als die EXIF-Angabe
    focal_px = _xmp_float(xmp, "drone-dji:CalibratedFocalLength")
    if focal_px and meta.width and meta.height:
        meta.focal_35mm = focal_px * KB_DIAGONAL_MM / math.hypot(meta.width, meta.height)
        meta.center_x = _xmp_float(xmp, "drone-dji:CalibratedOpticalCenterX")
        meta.center_y = _xmp_float(xmp, "drone-dji:CalibratedOpticalCenterY")

    status = xmp.get("drone-dji:LRFStatus", "Normal")
    if status.lower() == "normal":
        meta.lrf_lat = _xmp_float(xmp, "drone-dji:LRFTargetLat")
        meta.lrf_lon = _xmp_float(xmp, "drone-dji:LRFTargetLon")
        meta.lrf_distance = _xmp_float(xmp, "drone-dji:LRFTargetDistance")
        meta.lrf_abs_alt = _xmp_float(xmp, "drone-dji:LRFTargetAbsAlt")


def _apply_exif(meta: PhotoMetadata, tiff: bytes) -> None:
    try:
        ifd0, exif, gps = _parse_tiff(tiff)
    except (struct.error, IndexError, ValueError):
        return
    meta.make = _ascii(ifd0.get(_TAG_MAKE))
    meta.model = _ascii(ifd0.get(_TAG_MODEL))
    if not meta.width or not meta.height:
        meta.width = _first_int(exif.get(_TAG_PIXEL_X)) or meta.width
        meta.height = _first_int(exif.get(_TAG_PIXEL_Y)) or meta.height

    lat = _gps_degrees(gps.get(2), gps.get(1))
    lon = _gps_degrees(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        meta.lat, meta.lon = lat, lon
    altitude = _first_float(gps.get(6))
    if altitude is not None:
        below_sea = _first_int(gps.get(5)) == 1
        meta.abs_alt = -altitude if below_sea else altitude

    focal_35 = _first_int(exif.get(_TAG_FOCAL_35MM))
    if focal_35:
        meta.focal_35mm = float(focal_35)
    else:
        # Aus echter Brennweite und Sensorgröße (Pixel je Einheit der Fokusebene) ableiten
        focal_mm = _first_float(exif.get(_TAG_FOCAL_LENGTH))
        xres = _first_float(exif.get(_TAG_FOCAL_PLANE_XRES))
        unit_mm = _FOCAL_PLANE_UNIT_MM.get(_first_int(exif.get(_TAG_FOCAL_PLANE_UNIT)) or 2)
        if focal_mm and xres and unit_mm and meta.width and meta.height:
            pixel_mm = unit_mm / xres
            sensor_diag = math.hypot(meta.width, meta.height) * pixel_mm
            meta.focal_35mm = focal_mm * KB_DIAGONAL_MM / sensor_diag


def _parse_tiff(tiff: bytes) -> tuple[dict, dict, dict]:
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        raise ValueError("Ungültiger TIFF-Header")
    (ifd0_offset,) = struct.unpack(endian + "I", tiff[4:8])
    ifd0 = _read_ifd(tiff, ifd0_offset, endian)
    exif_offset = _first_int(ifd0.get(_TAG_EXIF_IFD))
    gps_offset = _first_int(ifd0.get(_TAG_GPS_IFD))
    exif = _read_ifd(tiff, exif_offset, endian) if exif_offset else {}
    gps = _read_ifd(tiff, gps_offset, endian) if gps_offset else {}
    return ifd0, exif, gps


def _read_ifd(tiff: bytes, offset: int, endian: str) -> dict:
    (count,) = struct.unpack(endian + "H", tiff[offset : offset + 2])
    entries = {}
    for i in range(count):
        start = offset + 2 + 12 * i
        tag, typ, n = struct.unpack(endian + "HHI", tiff[start : start + 8])
        size = _TYPE_SIZES.get(typ)
        if size is None:
            continue
        total = size * n
        if total <= 4:
            raw = tiff[start + 8 : start + 8 + total]
        else:
            (value_offset,) = struct.unpack(endian + "I", tiff[start + 8 : start + 12])
            raw = tiff[value_offset : value_offset + total]
        if len(raw) < total:
            continue
        entries[tag] = _decode(raw, typ, n, endian)
    return entries


def _decode(raw: bytes, typ: int, n: int, endian: str):
    if typ in (2, 7):  # ASCII / UNDEFINED
        return raw
    if typ in (5, 10):  # (S)RATIONAL
        fmt = "I" if typ == 5 else "i"
        nums = struct.unpack(f"{endian}{2 * n}{fmt}", raw)
        return [nums[i] / nums[i + 1] if nums[i + 1] else 0.0 for i in range(0, len(nums), 2)]
    fmt = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d"}[typ]
    return list(struct.unpack(f"{endian}{n}{fmt}", raw))


def _ascii(value) -> str:
    if not isinstance(value, bytes):
        return ""
    return value.split(b"\x00", 1)[0].decode("latin-1").strip()


def _first_int(value) -> int | None:
    if isinstance(value, list) and value:
        return int(value[0])
    return None


def _first_float(value) -> float | None:
    if isinstance(value, list) and value:
        return float(value[0])
    return None


def _gps_degrees(dms, ref) -> float | None:
    if not isinstance(dms, list) or len(dms) < 3:
        return None
    degrees = dms[0] + dms[1] / 60.0 + dms[2] / 3600.0
    if _ascii(ref).upper() in ("S", "W"):
        degrees = -degrees
    return degrees


# ----------------------------------------------------------------------
# Geometrie
# ----------------------------------------------------------------------

Vector = tuple[float, float, float]


def camera_ray(yaw: float, pitch: float, roll: float, x_norm: float = 0.0, y_norm: float = 0.0) -> Vector:
    """Einheitsvektor (Ost, Nord, oben) durch einen Bildpunkt.

    ``x_norm``/``y_norm`` sind die Abstände vom Bildhauptpunkt geteilt durch die
    Brennweite in Pixeln (x nach rechts, y nach unten). Bei senkrechtem Blick
    nach unten zeigt die Bildoberkante in Blickrichtung ``yaw``.
    """
    psi, theta, phi = math.radians(yaw), math.radians(pitch), math.radians(roll)
    forward = (math.sin(psi) * math.cos(theta), math.cos(psi) * math.cos(theta), math.sin(theta))
    right = (math.cos(psi), -math.sin(psi), 0.0)
    up = _cross(right, forward)
    if phi:
        # Positive Rollung senkt die rechte Bildseite
        right, up = (
            tuple(r * math.cos(phi) - u * math.sin(phi) for r, u in zip(right, up)),
            tuple(u * math.cos(phi) + r * math.sin(phi) for r, u in zip(right, up)),
        )
    ray = tuple(f + x_norm * r - y_norm * u for f, r, u in zip(forward, right, up))
    return _normalize(ray)


def intersect_plane(ray: Vector, depth: float) -> tuple[float, float, float] | None:
    """Schnitt mit einer waagerechten Ebene ``depth`` Meter unter der Kamera.

    Gibt (Ost, Nord, Schrägentfernung) zurück, oder None, wenn der Strahl nicht nach unten zeigt.
    """
    if depth <= 0 or ray[2] >= -1e-9:
        return None
    t = depth / -ray[2]
    return ray[0] * t, ray[1] * t, t


def intersect_terrain(
    ray: Vector,
    camera_alt: float,
    terrain_height,
    max_distance: float = 10_000.0,
) -> tuple[float, float, float, float] | None:
    """Erster Schnitt des Strahls mit dem Gelände.

    ``terrain_height(ost, nord)`` liefert die Geländehöhe (gleicher Höhenbezug wie
    ``camera_alt``) oder None außerhalb des Modells. Die Erdkrümmung wird
    berücksichtigt. Gibt (Ost, Nord, Geländehöhe, Schrägentfernung) oder None zurück.
    """

    def clearance(t: float) -> tuple[float, float] | None:
        east, north = ray[0] * t, ray[1] * t
        ground = terrain_height(east, north)
        if ground is None:
            return None
        drop = (east * east + north * north) / (2 * EARTH_RADIUS_M)
        return camera_alt + ray[2] * t - (ground - drop), ground

    prev_t = 0.0
    t = 0.0
    while t < max_distance:
        # Schrittweite wächst mit der Entfernung: 2 m nah, höchstens 20 m
        t = min(max_distance, t + min(20.0, max(2.0, t * 0.01)))
        sample = clearance(t)
        if sample is None:
            prev_t = t
            continue
        if sample[0] <= 0:
            low, high = prev_t, t
            for _ in range(20):
                mid = (low + high) / 2
                mid_sample = clearance(mid)
                if mid_sample is not None and mid_sample[0] <= 0:
                    high = mid
                else:
                    low = mid
            final = clearance(high) or sample
            return ray[0] * high, ray[1] * high, final[1], high
        prev_t = t
    return None


def azimuth(east: float, north: float) -> float:
    """Richtung in Grad (0 = Nord, im Uhrzeigersinn)."""
    return math.degrees(math.atan2(east, north)) % 360.0


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize(v: Vector) -> Vector:
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v)
