"""Menu-bar tray icon: Start/Stop, status, recent sessions, Open Recordings Folder, Quit.

No recording logic here (see CLAUDE.md's layer rules): this class only renders the menu
and forwards user actions as Qt signals. ``ui/app_controller.py`` decides what those
actions mean.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import SessionMetadata
from local_meeting_recorder.ui.icons import icon_for_state

_START_ENABLED_STATES = frozenset({RecordingState.IDLE, RecordingState.SAVED, RecordingState.ERROR})
_STOP_ENABLED_STATES = frozenset({RecordingState.PREPARING, RecordingState.RECORDING})

_STATUS_LABELS: dict[RecordingState, str] = {
    RecordingState.IDLE: "Idle",
    RecordingState.PREPARING: "Preparing…",
    RecordingState.RECORDING: "Recording",
    RecordingState.FINALIZING: "Finalizing…",
    RecordingState.SAVED: "Saved",
    RecordingState.ERROR: "Error",
}


class TrayController(QObject):
    start_requested = Signal()
    stop_requested = Signal()
    open_recordings_requested = Signal()
    recent_session_requested = Signal(str)  # session_id
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._current_state = RecordingState.IDLE
        self._tray = QSystemTrayIcon()
        self._tray.setIcon(icon_for_state(RecordingState.IDLE))
        self._tray.setToolTip("LocalMeetingRecorder")

        self._menu = QMenu()
        self._status_action = self._menu.addAction(_STATUS_LABELS[RecordingState.IDLE])
        self._status_action.setEnabled(False)
        self._menu.addSeparator()

        self._start_action = self._menu.addAction("Start Recording")
        self._start_action.triggered.connect(self.start_requested)
        self._stop_action = self._menu.addAction("Stop Recording")
        self._stop_action.triggered.connect(self.stop_requested)
        self._menu.addSeparator()

        self._recent_menu = self._menu.addMenu("Recent Recordings")
        self._recent_menu.setEnabled(False)

        open_action = self._menu.addAction("Open Recordings Folder")
        open_action.triggered.connect(self.open_recordings_requested)
        self._menu.addSeparator()

        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_requested)

        self._tray.setContextMenu(self._menu)
        self.set_state(RecordingState.IDLE)

    # -- presentation only: no business logic below this line ---------------------------

    def show(self) -> None:
        self._tray.show()

    def set_state(self, state: RecordingState, *, detail: str | None = None) -> None:
        self._current_state = state
        self._tray.setIcon(icon_for_state(state))
        label = _STATUS_LABELS[state]
        self._status_action.setText(label if not detail else f"{label}: {detail}")
        self._start_action.setEnabled(state in _START_ENABLED_STATES)
        self._stop_action.setEnabled(state in _STOP_ENABLED_STATES)

    def set_duration(self, elapsed_s: float) -> None:
        if self._current_state is not RecordingState.RECORDING:
            return
        minutes, seconds = divmod(max(0, int(elapsed_s)), 60)
        hours, minutes = divmod(minutes, 60)
        text = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        self._status_action.setText(f"Recording — {text}")

    def set_recent_sessions(self, sessions: Sequence[SessionMetadata]) -> None:
        self._recent_menu.clear()
        self._recent_menu.setEnabled(bool(sessions))
        for metadata in sessions:
            label = metadata.title or metadata.session_id
            action = self._recent_menu.addAction(label)
            action.triggered.connect(self._make_recent_session_handler(metadata.session_id))

    def _make_recent_session_handler(self, session_id: str) -> Callable[[bool], None]:
        def handler(checked: bool = False) -> None:
            self.recent_session_requested.emit(session_id)

        return handler

    def show_notification(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message)

    @property
    def is_visible(self) -> bool:
        return self._tray.isVisible()
