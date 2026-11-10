"""Tray icons rendered with ``QPainter`` — no bundled image assets to go stale.

A plain colored dot is enough to distinguish Idle/Busy/Recording at the tiny size a menu
bar renders icons at; the menu's status text carries the actual detail.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from local_meeting_recorder.domain.recording_state import RecordingState

_SIZE = 32

_COLOR_BY_STATE: dict[RecordingState, QColor] = {
    RecordingState.IDLE: QColor(142, 142, 147),  # system gray
    RecordingState.PREPARING: QColor(255, 159, 10),  # orange
    RecordingState.RECORDING: QColor(255, 59, 48),  # red
    RecordingState.FINALIZING: QColor(255, 159, 10),  # orange
    RecordingState.SAVED: QColor(52, 199, 89),  # green
    RecordingState.ERROR: QColor(255, 59, 48),  # red
}

_cache: dict[RecordingState, QIcon] = {}


def icon_for_state(state: RecordingState) -> QIcon:
    cached = _cache.get(state)
    if cached is not None:
        return cached
    color = _COLOR_BY_STATE[state]
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    margin = 4
    painter.drawEllipse(margin, margin, _SIZE - 2 * margin, _SIZE - 2 * margin)
    painter.end()
    icon = QIcon(pixmap)
    _cache[state] = icon
    return icon
