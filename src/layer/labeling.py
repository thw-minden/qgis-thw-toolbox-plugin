from qgis.core import (
    Qgis,
    QgsPalLayerSettings,
    QgsProperty,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import Qt

from ..logging_utils import get_logger

logger = get_logger(__name__)

# 1 pt in Millimetern: die Schriftgröße je Zeichen steht in pt, die Beschriftung rechnet in mm
PT_TO_MM = 25.4 / 72
_SIZE = QgsPalLayerSettings.Property.Size
# QgsPropertyCollection erwartet den Schlüssel als int (unter PyQt6 ist das Enum keiner)
_SIZE_PROPERTY = _SIZE if isinstance(_SIZE, int) else _SIZE.value


def label_size_pt(feat, settings) -> float:
    """Schriftgröße der Beschriftung eines Zeichens in pt (ohne eigene: die aus den Einstellungen)."""
    idx = feat.fields().indexFromName("label_size")
    value = feat.attribute(idx) if idx >= 0 else None
    if value is None or (hasattr(value, "isNull") and value.isNull()) or float(value) <= 0:
        return round(settings.label_font_size_mm / PT_TO_MM, 1)
    return float(value)


def apply_labeling(layer: QgsVectorLayer, settings) -> None:
    """Configure feature labels on `layer` based on `settings`.

    Labels read from the `label` field, gated by `show_label = 1`. The
    expression-based gating uses dataDefinedProperties so the user can
    toggle individual features on/off without re-rendering.

    `settings` must expose: `label_font_size_mm`, `label_buffer_size_mm`,
    `label_enable`. Failures are caught (not raised) so a labeling bug
    can never block layer rendering.
    """
    try:
        field_names = [field.name() for field in layer.fields()]
        if "label" not in field_names or "show_label" not in field_names:
            logger.debug("Label-Felder nicht verfügbar, überspringe Labeling")
            return

        label_settings = QgsPalLayerSettings()
        label_settings.setFormat(_build_text_format(settings))

        label_settings.fieldName = "label"
        label_settings.isExpression = False

        label_props = label_settings.dataDefinedProperties()
        _apply_show_gating(label_settings, label_props, settings)
        _apply_placement(label_settings)
        _apply_offset(label_settings, label_props)
        _apply_font_size(label_props, settings)

        layer.setLabelsEnabled(True)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))

        logger.debug("Labeling erfolgreich konfiguriert")

    except Exception:
        logger.exception("Fehler beim Konfigurieren des Labelings")


def _build_text_format(settings) -> QgsTextFormat:
    """Bold black text with white halo buffer — readable on any basemap."""
    text_format = QgsTextFormat()
    text_format.setSize(settings.label_font_size_mm)
    text_format.setSizeUnit(Qgis.RenderUnit.Millimeters)
    text_format.setColor(Qt.GlobalColor.black)

    font = text_format.font()
    font.setBold(True)
    text_format.setFont(font)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(settings.label_buffer_size_mm)
    buffer_settings.setSizeUnit(Qgis.RenderUnit.Millimeters)
    buffer_settings.setColor(Qt.GlobalColor.white)
    text_format.setBuffer(buffer_settings)
    return text_format


def _apply_show_gating(label_settings: QgsPalLayerSettings, label_props, settings) -> None:
    """Wire the per-feature show/hide expression. Falls back to field-level expr on error."""
    expr = 'CASE WHEN "show_label" = 1 AND "label" IS NOT NULL AND "label" <> \'\' THEN 1 ELSE 0 END'
    try:
        show_property = QgsProperty.fromExpression(expr)
        if settings.label_enable:
            label_props.setProperty(QgsPalLayerSettings.Property.Show, show_property)
        else:
            label_props.setProperty(QgsPalLayerSettings.Property.Show, False)
    except Exception:
        logger.exception("Fehler beim Setzen der Expression")
        # Fallback: switch to expression-as-field — labels render conditionally
        try:
            label_settings.isExpression = True
            label_settings.fieldName = 'CASE WHEN "show_label" = 1 THEN "label" ELSE \'\' END'
            logger.debug("Alternative Label-Expression als Feld-Expression gesetzt")
        except Exception as e2:
            logger.warning("Auch alternative Expression fehlgeschlagen: %s", e2)


def _apply_placement(label_settings: QgsPalLayerSettings) -> None:
    """Place label below the point. Handles both modern (>=3.26) and older QGIS APIs."""
    try:
        if hasattr(Qgis, "LabelPlacement") and hasattr(Qgis.LabelPlacement, "OverPoint"):
            label_settings.placement = Qgis.LabelPlacement.OverPoint
        else:
            label_settings.placement = QgsPalLayerSettings.OverPoint

        if hasattr(Qgis, "LabelQuadrantPosition"):
            label_settings.quadOffset = Qgis.LabelQuadrantPosition.Below
        elif hasattr(QgsPalLayerSettings, "BottomMiddle"):
            label_settings.quadOffset = QgsPalLayerSettings.BottomMiddle
        elif hasattr(QgsPalLayerSettings, "Bottom"):
            label_settings.quadOffset = QgsPalLayerSettings.Bottom
        else:
            label_settings.quadOffset = 0
    except Exception as e:
        logger.warning("Position festsetzen fehlgeschlagen: %s", e)


def _apply_offset(label_settings: QgsPalLayerSettings, label_props) -> None:
    """Offset label below the symbol by its size — keeps label clear of the marker."""
    try:
        if hasattr(Qgis, "RenderUnit") and hasattr(Qgis.RenderUnit, "Points"):
            label_settings.offsetUnits = Qgis.RenderUnit.Points
        size_property = QgsProperty.fromExpression("array(0, size * 0.3)")
        label_props.setProperty(QgsPalLayerSettings.Property.OffsetXY, size_property)
    except Exception as e:
        logger.warning("Fehler bei der Festsetzung der Position: %s", e)


def _apply_font_size(label_props, settings) -> None:
    """Schriftgröße je Zeichen (Feld ``label_size`` in pt), sonst die aus den Einstellungen (mm)."""
    try:
        expr = f'coalesce("label_size" * {PT_TO_MM:.6f}, {float(settings.label_font_size_mm)})'
        label_props.setProperty(_SIZE_PROPERTY, QgsProperty.fromExpression(expr))
    except Exception as e:
        logger.warning("Schriftgröße je Zeichen nicht gesetzt: %s", e)
