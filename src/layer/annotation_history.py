"""Undo/redo for the annotation layer within the current QGIS session.

Every change made through ``layer.annotations`` records a snapshot of the layer *before*
the change. A snapshot is the layer's own XML (all items with their ids — QGIS restores
them with the same ids, so descriptions, measurement labels and the plugin's item metadata
keep matching) plus that metadata. Nothing is persisted: the history starts empty with
every session and is cleared when a project is closed or loaded.
"""

from dataclasses import dataclass

from qgis.core import QgsProject, QgsReadWriteContext
from qgis.PyQt.QtXml import QDomDocument

from ..logging_utils import get_logger

logger = get_logger(__name__)

_MAX_STEPS = 100


@dataclass
class _Snapshot:
    layer_id: str
    label: str  # what the change was, e.g. "Punkt setzen" — shown after undo/redo
    xml: str
    meta: str


def _capture(layer, meta_property: str, label: str) -> _Snapshot:
    doc = QDomDocument()
    element = doc.createElement("maplayer")
    doc.appendChild(element)
    layer.writeXml(element, doc, QgsReadWriteContext())
    return _Snapshot(layer.id(), label, doc.toString(), str(layer.customProperty(meta_property, "")))


def _restore(layer, meta_property: str, snapshot: _Snapshot) -> None:
    doc = QDomDocument()
    doc.setContent(snapshot.xml)
    layer.readXml(doc.documentElement(), QgsReadWriteContext())  # replaces all items, keeps ids
    layer.setCustomProperty(meta_property, snapshot.meta)
    layer.triggerRepaint()
    QgsProject.instance().setDirty(True)


class AnnotationHistory:
    """Undo/redo stacks of annotation layer snapshots."""

    def __init__(self, meta_property: str, max_steps: int = _MAX_STEPS):
        self._meta_property = meta_property
        self._max_steps = max_steps
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def capture(self, layer, label: str) -> _Snapshot:
        """State of ``layer`` before a change (pass it to ``commit`` once the change is done)."""
        return _capture(layer, self._meta_property, label)

    def commit(self, layer, before: _Snapshot) -> None:
        """Record ``before`` as an undo step — unless the change left the layer untouched."""
        after = _capture(layer, self._meta_property, before.label)
        if after.xml == before.xml and after.meta == before.meta:
            return
        self._undo.append(before)
        del self._undo[: -self._max_steps]
        self._redo.clear()  # a new change invalidates everything that was undone

    def undo(self) -> str | None:
        """Revert the last change. Returns its label, or None if there was nothing (left) to undo."""
        return self._step(self._undo, self._redo)

    def redo(self) -> str | None:
        """Re-apply the last undone change. Returns its label, or None."""
        return self._step(self._redo, self._undo)

    def _step(self, source: list[_Snapshot], target: list[_Snapshot]) -> str | None:
        while source:
            snapshot = source.pop()
            layer = QgsProject.instance().mapLayer(snapshot.layer_id)
            if layer is None:
                continue  # the layer was removed meanwhile — skip its steps
            target.append(_capture(layer, self._meta_property, snapshot.label))
            try:
                _restore(layer, self._meta_property, snapshot)
            except Exception:
                logger.exception("Annotationen konnten nicht wiederhergestellt werden")
                return None
            return snapshot.label
        return None
