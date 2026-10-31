"""QGIS-Ausdrücke für Beschriftungen und Maße der Objektplanung.

Alles hier ist reiner Ausdruckstext, ohne eigene Ausdrucksfunktionen des Plugins: So zeichnet ein
Projekt seine Koordinaten und Maße auch auf einem Rechner ohne THW Toolbox.
"""

# Buchstaben des Meldegitters wie in ``layout.mgrs_grid`` (von dort nicht importierbar: das zieht über die
# Legende die Objektplanungs-Layer nach, die diese Ausdrücke selbst brauchen)
BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"
COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
ROW_ODD = "ABCDEFGHJKLMNPQRSTUV"
ROW_EVEN = "FGHJKLMNPQRSTUVABCDE"

# In Beschreibungen von Punkten und in Texten: wird auf der Karte durch die UTMREF-Koordinate ersetzt
POSITION_PLACEHOLDER = "$POS"
# Projektvariable mit der UTMREF-Genauigkeit in Metern (10, 1 oder 0,1); fehlt sie, gilt 1 m
MGRS_RESOLUTION_VARIABLE = "thw_utmref_aufloesung"


def _utm_crs_expr(geom_expr: str) -> str:
    """'EPSG:326xx' bzw. '327xx' der UTM-Zone, in der die Mitte von ``geom_expr`` (Layer-CRS) liegt."""
    center = f"transform(centroid({geom_expr}), @layer_crs, 'EPSG:4326')"
    return (
        f"'EPSG:' || to_string(if(y({center}) >= 0, 32600, 32700) + min(floor((x({center}) + 180) / 6) + 1, 60))"
    )


def metric_length_expr(geom_expr: str) -> str:
    """Länge in Metern – über die UTM-Zone gemessen, auch wenn das Projekt-CRS Grad oder Web-Mercator ist."""
    return f"length(transform({geom_expr}, @layer_crs, {_utm_crs_expr(geom_expr)}))"


def meters_text_expr(value_expr: str) -> str:
    """Meter als Text wie in den Details: „12,5 m“, ab 1 km „1,25 km“."""
    return (
        f"if({value_expr} >= 1000, replace(to_string(round({value_expr} / 1000, 2)), '.', ',') || ' km', "
        f"replace(to_string(round({value_expr}, 1)), '.', ',') || ' m')"
    )


def utmref_expr(point_expr: str) -> str:
    """UTMREF wie ``util.coordinates.to_mgrs``, z. B. ``32U MC 94254 93123`` bei 1 m.

    Die Genauigkeit kommt aus der Projektvariable ``@thw_utmref_aufloesung``: 10 m → 4 Stellen,
    1 m → 5, 0,1 m → 6 je Rechts- und Hochwert; abgeschnitten, nicht gerundet.
    """
    cols = ", ".join(f"'{c}'" for c in COL_SETS)
    return (
        f"with_variable('_ll', transform({point_expr}, @layer_crs, 'EPSG:4326'), "
        "with_variable('_zone', min(floor((x(@_ll) + 180) / 6) + 1, 60), "
        f"with_variable('_utm', transform({point_expr}, @layer_crs, "
        "'EPSG:' || to_string(if(y(@_ll) >= 0, 32600, 32700) + @_zone)), "
        "with_variable('_e', round(x(@_utm), 3), "
        "with_variable('_n', round(y(@_utm), 3), "
        f"with_variable('_res', coalesce(@{MGRS_RESOLUTION_VARIABLE}, 1), "
        "with_variable('_digits', 5 - round(log10(@_res)), "
        "to_string(@_zone) "
        f"|| substr('{BAND_LETTERS}', min(max(floor((y(@_ll) + 80) / 8), 0), 19) + 1, 1) || ' ' "
        f"|| substr(array_get(array({cols}), (@_zone - 1) % 3), max(0, min(7, floor(@_e / 100000) - 1)) + 1, 1) "
        f"|| substr(if(@_zone % 2 = 1, '{ROW_ODD}', '{ROW_EVEN}'), floor((@_n % 2000000) / 100000) + 1, 1) "
        "|| ' ' || lpad(to_string(floor((@_e % 100000) / @_res + 0.000001)), @_digits, '0') "
        "|| ' ' || lpad(to_string(floor((@_n % 100000) / @_res + 0.000001)), @_digits, '0')"
        ")))))))"
    )


def note_label_expr() -> str:
    """Beschriftung eines Punkts bzw. Texts: die Bezeichnung, ``$POS`` darin durch die UTMREF ersetzt."""
    text = "nullif(\"bezeichnung\", '')"
    return (
        f"if(strpos({text}, '{POSITION_PLACEHOLDER}') > 0, "
        f"replace({text}, '{POSITION_PLACEHOLDER}', {utmref_expr('$geometry')}), {text})"
    )
