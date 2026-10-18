from qgis.core import QgsField
from qgis.PyQt.QtCore import QVariant

# Qt6 compatibility: prefer QMetaType.Type (QGIS 4 / Qt6 native), fall back to
# QVariant.Type for QGIS 3 / Qt5. Some PyQt6 builds shipped with QGIS 4 are
# missing several QVariant.Type values (notably Bool), so addAttributes() can
# silently drop fields whose type constant resolves to something invalid —
# leaving the GeoPackage with just the implicit `fid` column.
try:
    from qgis.PyQt.QtCore import QMetaType

    _T_STRING = QMetaType.Type.QString
    _T_INT = QMetaType.Type.Int
    _T_DOUBLE = QMetaType.Type.Double
    _T_BOOL = QMetaType.Type.Bool
except (ImportError, AttributeError):
    _T_STRING = QVariant.String
    _T_INT = QVariant.Int
    _T_DOUBLE = QVariant.Double
    _T_BOOL = getattr(QVariant, "Bool", QVariant.Int)


# Single source of truth for the THW Toolbox feature schema.
# All layer creation, field migration, and runtime field checks derive
# from this list. Order matters for layer creation; do not reorder
# without handling existing GeoPackage migration.
LAYER_FIELDS: list[tuple[str, object]] = [
    ("name", _T_STRING),
    ("svg_path", _T_STRING),
    ("svg_content", _T_STRING),
    ("size", _T_DOUBLE),
    ("scale_with_map", _T_BOOL),
    ("unique_id", _T_STRING),
    ("label", _T_STRING),
    ("show_label", _T_BOOL),
    ("white_background", _T_BOOL),
    ("rotation", _T_DOUBLE),
    ("origin_x", _T_INT),
    ("origin_y", _T_INT),
    # Hintergrund als JSON (layer.background); white_background bleibt für ältere Plugin-Versionen
    ("background", _T_STRING),
    # Schriftgröße der Beschriftung in pt (leer = aus den Einstellungen)
    ("label_size", _T_DOUBLE),
    # Mit der Karte skalierte Zeichen wachsen nur bis max_scale bzw. schrumpfen nur bis min_scale
    # (Maßstabszahlen, leer = unbegrenzt; siehe layer.renderer.scale_limits)
    ("max_scale", _T_DOUBLE),
    ("min_scale", _T_DOUBLE),
]


def build_qgs_fields() -> list[QgsField]:
    """Fresh QgsField objects for layer creation / migration."""
    return [QgsField(name, vtype) for name, vtype in LAYER_FIELDS]


def field_types_dict() -> dict[str, object]:
    """{field_name: type_constant} for runtime field-presence checks."""
    return dict(LAYER_FIELDS)
