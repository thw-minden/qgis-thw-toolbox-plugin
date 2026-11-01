"""Import the bundled THW SVGs into QGIS' default symbol style library.

Symbols are written to ``QgsStyle.defaultStyle()`` so they appear in QGIS'
standard symbol picker (Voreinstellungen) across all projects, not just the
current one. Each symbol carries a ``THW`` tag plus a category tag derived
from the SVG's top-level folder, which makes them filterable in the symbol
manager and removable later.

Naming: the full SVG path is flattened with spaces, with underscores in
folder names replaced by spaces. So ``THW_Einheiten/Gruppenführer.svg``
becomes ``THW Einheiten Gruppenführer`` — that lets a user search the
symbol selector for "THW Einheiten" and hit it directly.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from typing import Callable, Optional

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMarkerSymbol,
    QgsMessageLog,
    QgsStyle,
    QgsSvgMarkerSymbolLayer,
)

from ..logging_utils import get_logger

logger = get_logger(__name__)

THW_TAG = "THW"
DEFAULT_SIZE_MM = 8.0

ProgressCb = Callable[[int, int], bool]


def _collect_svg_files(plugin_dir: str) -> list[tuple[str, list[str]]]:
    """Return ``(absolute_path, [folder, …, filename])`` tuples for each SVG."""
    base = os.path.join(plugin_dir, "svgs")
    result: list[tuple[str, list[str]]] = []
    if not os.path.isdir(base):
        return result
    for root, _dirs, files in os.walk(base):
        for fname in files:
            if not fname.lower().endswith(".svg"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, base).replace("\\", "/")
            result.append((full, rel.split("/")))
    result.sort(key=lambda item: item[1])
    return result


def _symbol_name(parts: list[str]) -> str:
    pretty = [os.path.splitext(p)[0].replace("_", " ") for p in parts]
    return " ".join(pretty)


def _category_tag(parts: list[str]) -> str:
    return os.path.splitext(parts[0])[0].replace("_", " ")


def _legacy_symbol_name(parts: list[str]) -> str:
    """Name format used by an earlier version of this module.

    Kept around so ``remove_styles`` cleans up entries written by a previous
    install that used the ``THW · Einheiten · Gruppe`` separator scheme.
    """
    pretty = [os.path.splitext(p)[0].replace("_", " ") for p in parts]
    return " · ".join(["THW"] + pretty)


def _expected_names(plugin_dir: str) -> set[str]:
    """All symbol names this version *would* write for the bundled SVGs."""
    return {_symbol_name(parts) for _full, parts in _collect_svg_files(plugin_dir)}


def _expected_legacy_names(plugin_dir: str) -> set[str]:
    return {_legacy_symbol_name(parts) for _full, parts in _collect_svg_files(plugin_dir)}


def _expected_tags(plugin_dir: str) -> set[str]:
    """All tag names the import would create, plus the marker tag."""
    tags = {THW_TAG}
    for _full, parts in _collect_svg_files(plugin_dir):
        tags.add(_category_tag(parts))
    return tags


def _count_present(style: QgsStyle, names) -> int:
    """Count how many ``names`` are present in ``style``.

    Uses ``symbol(name)`` per entry rather than ``symbolNames()``: the
    latter has been observed to return inconsistent results on QGIS 4
    (an empty list even when the symbols are clearly there in the style
    manager), while the per-name lookup is reliable.
    """
    return sum(1 for name in names if style.symbol(name) is not None)


def status(plugin_dir: str) -> tuple[int, int]:
    """Return ``(imported_count, total_count)``.

    Detection is name-based: we compute the names this version *would* write
    for the bundled SVGs and check which of them exist in the default style.
    """
    style = QgsStyle.defaultStyle()
    expected = _expected_names(plugin_dir)
    if not expected:
        return 0, 0
    return _count_present(style, expected), len(expected)


def _save_symbol_to_style(
    style: QgsStyle,
    name: str,
    symbol: QgsMarkerSymbol,
    tags: list[str],
) -> bool:
    """Persist one symbol to the default style's SQLite DB via ``saveSymbol``.

    ``saveSymbol`` does a plain INSERT against ``symbol.name UNIQUE`` — not an
    UPSERT. A pre-existing row therefore kills the write silently. The naive
    check ``style.symbol(name)`` only sees the in-memory cache, which on this
    user's QGIS 4 ends up out of sync with the DB after a restart: thousands
    of rows present in the SQLite file, but ``mSymbols`` only holds the
    bundled defaults. So we check the DB directly via ``symbolId`` (which
    queries the row table, not the cache) and explicitly delete the stale row
    with ``remove(StyleEntity, id)`` before saving.
    """
    sid = style.symbolId(name)
    if sid > 0:
        try:
            style.remove(QgsStyle.StyleEntity.SymbolEntity, sid)
        except Exception as e:
            logger.debug("Konnte alten Symbol-Eintrag %s (id=%s) nicht entfernen: %s", name, sid, e)
    try:
        return bool(style.saveSymbol(name, symbol.clone(), False, list(tags)))
    except Exception as exc:
        QgsMessageLog.logMessage(
            f"THW Toolbox: saveSymbol('{name}') raised: {exc}",
            "THW Toolbox",
            Qgis.MessageLevel.Warning,
        )
        return False


def import_styles(
    plugin_dir: str,
    on_progress: Optional[ProgressCb] = None,
) -> tuple[int, int]:
    """Add (or refresh) every bundled SVG to the default symbol style.

    ``on_progress(done, total)`` is called after each entry; return ``False``
    from it to abort. Returns ``(written, total)`` where ``written`` is the
    number of symbols actually present in the style library after the run
    (verified via ``symbolNames()``, not just the API return value).
    """
    style = QgsStyle.defaultStyle()
    files = _collect_svg_files(plugin_dir)
    total = len(files)
    failed: list[str] = []

    for i, (full, parts) in enumerate(files, start=1):
        symbol = QgsMarkerSymbol.createSimple({})
        layer = QgsSvgMarkerSymbolLayer(full, DEFAULT_SIZE_MM, 0)
        symbol.changeSymbolLayer(0, layer)
        name = _symbol_name(parts)
        tags = [THW_TAG, _category_tag(parts)]
        if not _save_symbol_to_style(style, name, symbol, tags):
            failed.append(name)
        if on_progress is not None and not on_progress(i, total):
            break

    # Verify by per-name presence in the style. After a successful
    # saveSymbol the entry is in both the SQLite DB and the in-memory
    # cache; ``style.symbol(name)`` is the cheapest reliable check.
    expected = _expected_names(plugin_dir)
    written = _count_present(style, expected)

    if failed:
        QgsMessageLog.logMessage(
            f"THW Toolbox: {len(failed)} Symbole konnten nicht geschrieben werden. Erstes Beispiel: {failed[0]}",
            "THW Toolbox",
            Qgis.MessageLevel.Warning,
        )

    return written, total


def rehydrate_cache(plugin_dir: str) -> int:
    """Re-populate the default style's in-memory cache from the SQLite DB.

    Workaround for a QGIS-side quirk we hit on this user's QGIS 4: at
    startup, ``QgsStyle::load()`` leaves ``mSymbols`` sparse — thousands of
    rows live in ``symbology-style.db`` but only the bundled defaults end
    up in the cache. Anything we imported in a previous session vanishes
    from the symbol picker until the user clicks "Stile importieren" again.

    We probe the DB with a single bulk query for our expected names, and for
    each one missing from the cache we construct the marker symbol from the
    on-disk SVG and ``addSymbol`` it (cache-only — no DB write, no signals).
    The picker rebuilds its model on next open and picks them up.

    Returns the count of symbols injected. No-op when the DB is empty of our
    names (user deliberately ran "Stile entfernen" or never imported) or
    when the cache is already complete.
    """
    files = _collect_svg_files(plugin_dir)
    if not files:
        return 0

    style = QgsStyle.defaultStyle()
    name_to_path = {_symbol_name(parts): full for full, parts in files}

    db_path = QgsApplication.userStylePath()
    try:
        # `with` on the connection only commits/rolls back; `closing` actually closes it
        with closing(sqlite3.connect(db_path)) as con:
            placeholders = ",".join("?" * len(name_to_path))
            # placeholders is only "?,?,?…" derived from len() — values are bound below.
            cur = con.execute(
                f"SELECT name FROM symbol WHERE name IN ({placeholders})",  # nosec B608
                list(name_to_path.keys()),
            )
            db_present = {row[0] for row in cur}
    except sqlite3.Error as exc:
        QgsMessageLog.logMessage(
            f"THW Toolbox: rehydrate DB probe failed: {exc}",
            "THW Toolbox",
            Qgis.MessageLevel.Warning,
        )
        return 0

    if not db_present:
        return 0

    cached = set(style.symbolNames())
    missing = db_present - cached
    if not missing:
        return 0

    count = 0
    for name in missing:
        full = name_to_path[name]
        symbol = QgsMarkerSymbol.createSimple({})
        layer = QgsSvgMarkerSymbolLayer(full, DEFAULT_SIZE_MM, 0)
        symbol.changeSymbolLayer(0, layer)
        if style.addSymbol(name, symbol.clone(), False):
            count += 1
    return count


def remove_styles(plugin_dir: str) -> int:
    """Remove every imported symbol *and* the tags the import created.

    Returns the count of symbols removed. Both current-format names and
    the legacy ``THW · …`` names from an earlier version are handled.

    Note: category tags like ``Einheiten`` are removed wholesale — if the
    user happened to use the same tag name for their own unrelated
    symbols, those symbols survive but lose this tag. This is a
    deliberate trade-off to give the user a clean uninstall.
    """
    style = QgsStyle.defaultStyle()

    # 1) Symbols
    targets = _expected_names(plugin_dir) | _expected_legacy_names(plugin_dir)
    removed = 0
    for name in targets:
        if style.symbol(name) is None:
            continue
        if style.removeSymbol(name):
            removed += 1

    # 2) Tags — remove via the generic entity-by-id API. tagId() returns
    # -1 for unknown tags, in which case we silently skip.
    for tag_name in _expected_tags(plugin_dir):
        tag_id = style.tagId(tag_name)
        if tag_id < 0:
            continue
        try:
            style.remove(QgsStyle.StyleEntity.TagEntity, tag_id)
        except Exception:
            try:
                style.removeTag(tag_id)
            except Exception as e:
                logger.debug("Konnte Tag id=%s nicht entfernen: %s", tag_id, e)

    return removed
