"""Wires ``TrayController`` + ``CoordinatorBridge`` together.

This is the one place that knows the UI *flow* — check permissions, then show consent,
then start; disable invalid actions; surface errors and recovered sessions. No
recording-domain logic lives here: every decision is "when to show what" or "which
coordinator call to make next." ``RecordingCoordinator`` still owns every state
transition, session rule, and persistence decision.

``consent_fn``/``permission_guide_fn`` are injected so tests can drive the full
check-permissions -> consent -> start flow without a real modal dialog.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from local_meeting_recorder.application.recording_coordinator import RecordingCoordinator
from local_meeting_recorder.capture.protocols import PermissionStatus, WarningInfo
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import utc_now
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.ui.consent_dialog import request_consent
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.permission_guide import show_permission_guide
from local_meeting_recorder.ui.shell import open_path
from local_meeting_recorder.ui.tray import TrayController

log = get_logger(__name__)

ConsentFn = Callable[[], bool]
PermissionGuideFn = Callable[[PermissionStatus], None]


class AppController(QObject):
    def __init__(
        self,
        *,
        coordinator: RecordingCoordinator,
        bridge: CoordinatorBridge,
        tray: TrayController,
        session_store: SessionStore,
        settings: Settings,
        consent_fn: ConsentFn = request_consent,
        permission_guide_fn: PermissionGuideFn | None = None,
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._bridge = bridge
        self._tray = tray
        self._store = session_store
        self._settings = settings
        self._consent_fn = consent_fn
        self._permission_guide_fn = permission_guide_fn or show_permission_guide

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration)

        tray.start_requested.connect(self._on_start_requested)
        tray.stop_requested.connect(self._on_stop_requested)
        tray.open_recordings_requested.connect(self._on_open_recordings_requested)
        tray.recent_session_requested.connect(self._on_recent_session_requested)
        tray.quit_requested.connect(self._on_quit_requested)

        bridge.state_changed.connect(self._on_state_changed)
        bridge.operation_failed.connect(self._on_operation_failed)
        bridge.warning_raised.connect(self._on_warning_raised)
        bridge.permissions_checked.connect(self._on_permissions_checked)

        self._refresh_recent_sessions()
        self._apply_state(coordinator.state)

    def announce_recovered_sessions(self) -> None:
        recovered = self._coordinator.recovered_sessions
        if not recovered:
            return
        title = (
            "Interrupted Recording Found" if len(recovered) == 1 else "Interrupted Recordings Found"
        )
        self._tray.show_notification(
            title,
            f"{len(recovered)} recording(s) were interrupted and have been marked for "
            "review in your recordings folder.",
        )

    # -- tray-triggered actions ----------------------------------------------------------

    def _on_start_requested(self) -> None:
        if self._coordinator.state in (RecordingState.SAVED, RecordingState.ERROR):
            self._coordinator.reset()
            self._apply_state(self._coordinator.state)
        if self._coordinator.state is not RecordingState.IDLE:
            return
        self._bridge.check_permissions_async()

    def _on_permissions_checked(self, status: object, error: object) -> None:
        if error is not None:
            self._tray.show_notification("Permission Check Failed", str(error))
            return
        assert isinstance(status, PermissionStatus)
        if not status.all_granted:
            self._permission_guide_fn(status)
            return
        if not self._consent_fn():
            return
        if self._coordinator.state is not RecordingState.IDLE:
            return  # state moved on while the dialog was open; do not start twice
        self._bridge.start_recording_async(capture_microphone=self._settings.capture_microphone)

    def _on_stop_requested(self) -> None:
        if self._coordinator.state not in (RecordingState.PREPARING, RecordingState.RECORDING):
            return
        self._bridge.stop_recording_async()

    def _on_open_recordings_requested(self) -> None:
        open_path(self._store.paths.recordings_dir)

    def _on_recent_session_requested(self, session_id: str) -> None:
        open_path(self._store.session_dir(session_id))

    def _on_quit_requested(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # -- coordinator-triggered updates ---------------------------------------------------

    def _on_state_changed(self, state: object) -> None:
        assert isinstance(state, RecordingState)
        self._apply_state(state)
        if state is RecordingState.SAVED:
            self._refresh_recent_sessions()

    def _apply_state(self, state: RecordingState) -> None:
        detail = None
        if state is RecordingState.ERROR:
            last_error = self._coordinator.last_error
            detail = last_error[1] if last_error else None
        self._tray.set_state(state, detail=detail)
        if state is RecordingState.RECORDING:
            self._duration_timer.start()
        else:
            self._duration_timer.stop()

    def _update_duration(self) -> None:
        session = self._coordinator.current_session
        if session is None or session.started_at is None:
            return
        elapsed = (utc_now() - session.started_at).total_seconds()
        self._tray.set_duration(elapsed)

    def _on_operation_failed(self, code: str, message: str) -> None:
        log.error("recording operation failed", extra={"code": code})
        self._tray.show_notification("Recording Error", message)

    def _on_warning_raised(self, warning: object) -> None:
        assert isinstance(warning, WarningInfo)
        self._tray.show_notification("Warning", warning.message)

    def _refresh_recent_sessions(self) -> None:
        self._tray.set_recent_sessions(self._store.list_sessions()[:10])
