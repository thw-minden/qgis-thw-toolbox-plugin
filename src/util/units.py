"""Formatting of measured lengths and areas for display (German number format)."""


def format_number(value: float, decimals: int = 1) -> str:
    """German number format, e.g. 1234.5 → "1.234,5"."""
    return f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_meters(value: float) -> str:
    return format_number(value) + " m"


def format_area(square_meters: float) -> str:
    """Area in m², with hectares added from 1 ha on, e.g. "12.345,6 m² (1,23 ha)"."""
    text = format_number(square_meters) + " m²"
    if square_meters >= 10_000:
        text += f" ({format_number(square_meters / 10_000, 2)} ha)"
    return text
