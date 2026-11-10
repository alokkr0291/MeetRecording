"""AppController: the check-permissions -> consent -> start flow, and UI state wiring.

Real TrayController and CoordinatorBridge (so the actual Qt plumbing is exercised), a
FakeCaptureBackend-backed coordinator, and injected consent/permission-guide callables so
no real modal dialog needs to appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from local_meeting_recorder.application.recording_coordinator import RecordingCoordinator
from local_meeting_recorder.capture.fake import FakeCaptureBackend
from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    PermissionState,
    PermissionStatus,
)
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import CaptureError
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import SessionStatus
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.ui.app_controller import AppController
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.tray import TrayController
from tests.unit.ui.conftest import pump_until


class _Spy:
    def __init__(self, return_value: object = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.return_value = return_value

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.return_value


def _build(
    data_dir: Path,
    *,
    fake: FakeCaptureBackend | None = None,
    consent_fn: object = None,
    permission_guide_fn: object = None,
    recover_on_init: bool = False,
) -> tuple[AppController, TrayController, RecordingCoordinator, FakeCaptureBackend, SessionStore]:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    store = SessionStore(paths)
    backend = fake if fake is not None else FakeCaptureBackend()
    bridge = CoordinatorBridge()

    def factory(callbacks: CaptureCallbacks) -> FakeCaptureBackend:
        backend.callbacks = callbacks
        return backend

    settings = Settings(_env_file=None, data_dir=data_dir)
    coordinator = RecordingCoordinator(
        backend_factory=factory,
        session_store=store,
        settings=settings,
        callbacks=bridge.callbacks,
        recover_on_init=recover_on_init,
    )
    bridge.attach(coordinator)
    tray = TrayController()
    controller = AppController(
        coordinator=coordinator,
        bridge=bridge,
        tray=tray,
        session_store=store,
        settings=settings,
        consent_fn=consent_fn or _Spy(return_value=True),
        permission_guide_fn=permission_guide_fn,
    )
    return controller, tray, coordinator, backend, store


def test_permission_denied_shows_guide_and_never_starts(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.DENIED, microphone=PermissionState.GRANTED
        )
    )
    guide = _Spy()
    _controller, tray, coordinator, _fake, _store = _build(
        data_dir, fake=fake, permission_guide_fn=guide
    )

    tray.start_requested.emit()
    assert pump_until(lambda: len(guide.calls) == 1)
    assert coordinator.state is RecordingState.IDLE
    (status,) = guide.calls[0]
    assert isinstance(status, PermissionStatus)
    assert not status.screen.is_usable


def test_consent_declined_does_not_start(qapp: QApplication, data_dir: Path) -> None:
    consent = _Spy(return_value=False)
    _controller, tray, coordinator, fake, _store = _build(data_dir, consent_fn=consent)

    tray.start_requested.emit()
    assert pump_until(lambda: len(consent.calls) == 1)
    assert coordinator.state is RecordingState.IDLE
    assert fake.start_calls == []


def test_consent_accepted_starts_recording(qapp: QApplication, data_dir: Path) -> None:
    consent = _Spy(return_value=True)
    _controller, tray, coordinator, fake, _store = _build(data_dir, consent_fn=consent)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    assert len(fake.start_calls) == 1


def test_stop_requested_while_idle_is_ignored(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.stop_requested.emit()
    assert coordinator.state is RecordingState.IDLE
    assert fake.stop_calls == 0


def test_full_cycle_updates_tray_status_text(qapp: QApplication, data_dir: Path) -> None:
    # Polls the tray's own text, not coordinator.state: the coordinator can reach a new
    # state on its background thread slightly before the queued signal that updates the
    # tray has actually been delivered, so asserting text right after a state-only poll
    # is a race. Waiting for the text itself is what the test actually cares about.
    _controller, tray, coordinator, _fake, _store = _build(data_dir)

    tray.start_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Recording")
    assert coordinator.state is RecordingState.RECORDING

    tray.stop_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Saved")
    assert coordinator.state is RecordingState.SAVED


def test_start_after_saved_resets_and_starts_again(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    assert len(fake.start_calls) == 2


def test_error_state_shows_detail_in_tray(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_start_with=CaptureError("helper would not launch"))
    _controller, tray, coordinator, _fake, _store = _build(data_dir, fake=fake)

    tray.start_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Error: helper would not launch")
    assert coordinator.state is RecordingState.ERROR


def test_backend_crash_during_recording_updates_tray_to_error(
    qapp: QApplication, data_dir: Path
) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)

    fake.simulate_crash(message="helper died")
    assert pump_until(lambda: tray._status_action.text() == "Error: helper died")
    assert coordinator.state is RecordingState.ERROR


def test_duration_reflects_elapsed_time_since_start(qapp: QApplication, data_dir: Path) -> None:
    controller, tray, coordinator, _fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)

    controller._update_duration()
    assert tray._status_action.text().startswith("Recording — 0:0")


def test_announce_recovered_sessions_shows_notification(qapp: QApplication, data_dir: Path) -> None:
    _controller1, _tray1, coord1, _fake1, store = _build(data_dir, recover_on_init=False)
    orphaned_session = coord1.start_recording()
    assert orphaned_session is not None
    with store.recording_path(orphaned_session).open("ab") as fh:
        fh.write(b"some bytes")

    controller, tray, coordinator, _fake2, _store2 = _build(data_dir, recover_on_init=True)
    notifications: list[tuple[str, str]] = []
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]

    assert len(coordinator.recovered_sessions) == 1
    controller.announce_recovered_sessions()
    assert len(notifications) == 1
    assert "Interrupted" in notifications[0][0]


def test_announce_recovered_sessions_silent_when_nothing_was_recovered(
    qapp: QApplication, data_dir: Path
) -> None:
    controller, tray, _coordinator, _fake, _store = _build(data_dir, recover_on_init=True)
    notifications: list[tuple[str, str]] = []
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]
    controller.announce_recovered_sessions()
    assert notifications == []


def test_open_recordings_requested_opens_the_recordings_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", calls.append)
    _controller, tray, _coordinator, _fake, store = _build(data_dir)

    tray.open_recordings_requested.emit()
    assert calls == [store.paths.recordings_dir]


def test_recent_session_requested_opens_that_sessions_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", calls.append)
    _controller, tray, _coordinator, _fake, store = _build(data_dir)

    tray.recent_session_requested.emit("lmr_20261104T000000_aaaa")
    assert calls == [store.session_dir("lmr_20261104T000000_aaaa")]


def test_saved_recording_appears_in_recent_sessions(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, _fake, store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].status is SessionStatus.SAVED
