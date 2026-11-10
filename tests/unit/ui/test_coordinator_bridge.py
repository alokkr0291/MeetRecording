"""CoordinatorBridge: cross-thread coordinator calls re-emitted as Qt signals.

Uses a real background thread (that is the whole point of this class) and pumps the
event loop just long enough for the queued cross-thread signal to arrive.
"""

from __future__ import annotations

from pathlib import Path

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
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from tests.unit.ui.conftest import pump_until


def _build(
    data_dir: Path, *, fake: FakeCaptureBackend | None = None
) -> tuple[CoordinatorBridge, RecordingCoordinator]:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    store = SessionStore(paths)
    backend = fake if fake is not None else FakeCaptureBackend()
    bridge = CoordinatorBridge()

    def factory(callbacks: CaptureCallbacks) -> FakeCaptureBackend:
        backend.callbacks = callbacks
        return backend

    coordinator = RecordingCoordinator(
        backend_factory=factory,
        session_store=store,
        settings=Settings(_env_file=None, data_dir=data_dir),
        callbacks=bridge.callbacks,
        recover_on_init=False,
    )
    bridge.attach(coordinator)
    return bridge, coordinator


def test_start_then_stop_emits_state_changes_and_success(
    qapp: QApplication, data_dir: Path
) -> None:
    bridge, coordinator = _build(data_dir)
    states: list[RecordingState] = []
    successes: list[object] = []
    bridge.state_changed.connect(states.append)
    bridge.operation_succeeded.connect(successes.append)

    bridge.start_recording_async()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    assert pump_until(lambda: len(successes) == 1)
    successes.clear()

    bridge.stop_recording_async()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)
    assert pump_until(lambda: len(successes) == 1)

    assert RecordingState.PREPARING in states
    assert RecordingState.RECORDING in states
    assert RecordingState.FINALIZING in states
    assert RecordingState.SAVED in states


def test_start_failure_emits_operation_failed(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_start_with=CaptureError("boom", code="capture_error"))
    bridge, coordinator = _build(data_dir, fake=fake)
    failures: list[tuple[str, str]] = []
    bridge.operation_failed.connect(lambda code, message: failures.append((code, message)))

    bridge.start_recording_async()
    assert pump_until(lambda: len(failures) == 1)
    assert failures[0][0] == "capture_error"
    assert coordinator.state is RecordingState.ERROR


def test_check_permissions_async_emits_status(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.DENIED, microphone=PermissionState.GRANTED
        )
    )
    bridge, _coordinator = _build(data_dir, fake=fake)
    results: list[tuple[object, object]] = []
    bridge.permissions_checked.connect(lambda status, error: results.append((status, error)))

    bridge.check_permissions_async()
    assert pump_until(lambda: len(results) == 1)
    status, error = results[0]
    assert error is None
    assert isinstance(status, PermissionStatus)
    assert not status.all_granted


def test_stats_and_warning_callbacks_forward_as_signals(qapp: QApplication, data_dir: Path) -> None:
    bridge, coordinator = _build(data_dir)
    stats_seen: list[object] = []
    warnings_seen: list[object] = []
    bridge.stats_updated.connect(stats_seen.append)
    bridge.warning_raised.connect(warnings_seen.append)

    bridge.start_recording_async()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    fake = coordinator._backend  # type: ignore[attr-defined]
    fake.emit_stats(elapsed_s=2.0)
    fake.emit_warning("low_disk_space", "free disk space is low")

    assert pump_until(lambda: len(stats_seen) == 1 and len(warnings_seen) == 1)


def test_attach_required_before_async_calls(data_dir: Path) -> None:
    bridge = CoordinatorBridge()
    try:
        bridge.coordinator  # noqa: B018 - property access is the point of this assertion
    except RuntimeError as exc:
        assert "attach" in str(exc)
    else:
        raise AssertionError("expected RuntimeError before attach() is called")
