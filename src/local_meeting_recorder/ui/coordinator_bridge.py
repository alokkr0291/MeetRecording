"""Marshals ``RecordingCoordinator`` across threads for Qt.

Coordinator methods block — they talk to a subprocess with multi-second timeouts — so
every call here runs on a background thread. ``CoordinatorCallbacks`` fire on whichever
thread the backend's own reader thread uses. Both paths only ever *emit Qt signals*,
never touch a widget directly: Qt delivers a signal emitted from thread A to a slot whose
receiver object lives on thread B through that receiver's event loop (``AutoConnection``
becomes ``QueuedConnection`` automatically), which is the standard safe way to get data
from a worker thread onto the GUI thread.

Two-phase construction, same reason as ``RecordingCoordinator.backend_factory``: a
``CaptureBackend``'s callbacks and a ``RecordingCoordinator``'s callbacks are both fixed
at construction time, but this bridge's bound methods need to exist *before* the
coordinator does in order to be passed in. Build the bridge, read its ``.callbacks``,
construct the coordinator with them, then call ``bridge.attach(coordinator)``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from local_meeting_recorder.application.recording_coordinator import (
    CoordinatorCallbacks,
    RecordingCoordinator,
)
from local_meeting_recorder.capture.protocols import StatsInfo, WarningInfo
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import SessionMetadata
from local_meeting_recorder.infrastructure.logging_setup import get_logger

log = get_logger(__name__)


class CoordinatorBridge(QObject):
    state_changed = Signal(object)  # RecordingState
    stats_updated = Signal(object)  # StatsInfo
    warning_raised = Signal(object)  # WarningInfo
    operation_succeeded = Signal(object)  # SessionMetadata | None
    operation_failed = Signal(str, str)  # code, message
    permissions_checked = Signal(object, object)  # PermissionStatus | None, Exception | None
    permissions_requested = Signal(
        object, object
    )  # PermissionsRequestResult | None, Exception | None

    def __init__(self) -> None:
        super().__init__()
        self._coordinator: RecordingCoordinator | None = None

    @property
    def callbacks(self) -> CoordinatorCallbacks:
        """Pass this to ``RecordingCoordinator(callbacks=...)`` before calling ``attach``."""
        return CoordinatorCallbacks(
            on_state_changed=self._emit_state_changed,
            on_stats=self._emit_stats,
            on_warning=self._emit_warning,
        )

    def attach(self, coordinator: RecordingCoordinator) -> None:
        self._coordinator = coordinator

    @property
    def coordinator(self) -> RecordingCoordinator:
        if self._coordinator is None:
            raise RuntimeError("CoordinatorBridge.attach() was never called")
        return self._coordinator

    # -- async commands -----------------------------------------------------------------

    def start_recording_async(
        self,
        *,
        display_id: int | None = None,
        capture_microphone: bool | None = None,
        title: str | None = None,
    ) -> None:
        self._run_async(
            lambda: self.coordinator.start_recording(
                display_id=display_id, capture_microphone=capture_microphone, title=title
            )
        )

    def stop_recording_async(self) -> None:
        self._run_async(self.coordinator.stop_recording)

    def check_permissions_async(self, *, timeout: float | None = None) -> None:
        def run() -> None:
            try:
                status = self.coordinator.check_permissions(timeout=timeout)
            except Exception as exc:
                self.permissions_checked.emit(None, exc)
            else:
                self.permissions_checked.emit(status, None)

        threading.Thread(target=run, name="lmr-check-permissions", daemon=True).start()

    def request_permissions_async(self, *, timeout: float | None = None) -> None:
        def run() -> None:
            try:
                result = self.coordinator.request_permissions(timeout=timeout)
            except Exception as exc:
                self.permissions_requested.emit(None, exc)
            else:
                self.permissions_requested.emit(result, None)

        threading.Thread(target=run, name="lmr-request-permissions", daemon=True).start()

    def _run_async(self, call: Callable[[], SessionMetadata | None]) -> None:
        def run() -> None:
            try:
                result = call()
            except Exception as exc:
                code = getattr(exc, "code", "error")
                log.error("coordinator operation failed", extra={"code": code})
                self.operation_failed.emit(code, str(exc))
            else:
                self.operation_succeeded.emit(result)

        threading.Thread(target=run, name="lmr-coordinator-op", daemon=True).start()

    # -- callbacks (fire on the backend's own thread; re-emit only) ----------------------

    def _emit_state_changed(self, state: RecordingState) -> None:
        self.state_changed.emit(state)

    def _emit_stats(self, stats: StatsInfo) -> None:
        self.stats_updated.emit(stats)

    def _emit_warning(self, warning: WarningInfo) -> None:
        self.warning_raised.emit(warning)
