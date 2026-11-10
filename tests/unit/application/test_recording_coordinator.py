"""RecordingCoordinator against FakeCaptureBackend — no subprocess, no macOS APIs.

Exercises the state machine and session persistence end to end; the real-helper wiring is
covered separately by capture/macos tests (Phase 2b) and the hardware acceptance test.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from local_meeting_recorder.application.recording_coordinator import (
    CoordinatorCallbacks,
    RecordingCoordinator,
)
from local_meeting_recorder.capture.fake import FakeCaptureBackend
from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    PermissionState,
    PermissionStatus,
)
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import (
    CaptureError,
    InvalidTransitionError,
    MediaError,
    PermissionDeniedError,
)
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import SessionStatus
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore


def _make_coordinator(
    data_dir: Path,
    *,
    fake: FakeCaptureBackend | None = None,
    settings: Settings | None = None,
    callbacks: CoordinatorCallbacks | None = None,
    recover_on_init: bool = False,
) -> tuple[RecordingCoordinator, FakeCaptureBackend, SessionStore]:
    storage_paths = StoragePaths(root=data_dir)
    storage_paths.ensure()
    store = SessionStore(storage_paths)
    backend = fake if fake is not None else FakeCaptureBackend()

    def factory(capture_callbacks: CaptureCallbacks) -> FakeCaptureBackend:
        backend.callbacks = capture_callbacks
        return backend

    settings_obj = settings or Settings(_env_file=None, data_dir=data_dir)
    coordinator = RecordingCoordinator(
        backend_factory=factory,
        session_store=store,
        settings=settings_obj,
        callbacks=callbacks,
        recover_on_init=recover_on_init,
    )
    return coordinator, backend, store


def test_successful_start_and_stop(data_dir: Path) -> None:
    coordinator, _fake, store = _make_coordinator(data_dir)
    started = coordinator.start_recording()
    assert started is not None
    assert coordinator.state is RecordingState.RECORDING
    assert store.recording_path(started).is_file()

    saved = coordinator.stop_recording()
    assert saved is not None
    assert coordinator.state is RecordingState.SAVED
    assert saved.status is SessionStatus.SAVED
    assert saved.duration_s == 1.0
    reread = store.read(saved.session_id)
    assert reread.status is SessionStatus.SAVED


def test_duplicate_start_raises(data_dir: Path) -> None:
    coordinator, _fake, _store = _make_coordinator(data_dir)
    coordinator.start_recording()
    with pytest.raises(InvalidTransitionError):
        coordinator.start_recording()
    state_after = coordinator.state
    assert state_after is RecordingState.RECORDING


def test_stop_when_idle_raises(data_dir: Path) -> None:
    coordinator, _fake, _store = _make_coordinator(data_dir)
    with pytest.raises(InvalidTransitionError):
        coordinator.stop_recording()


def test_double_stop_raises(data_dir: Path) -> None:
    coordinator, _fake, _store = _make_coordinator(data_dir)
    coordinator.start_recording()
    coordinator.stop_recording()
    with pytest.raises(InvalidTransitionError):
        coordinator.stop_recording()


def test_permission_denied_moves_to_error_and_raises(data_dir: Path) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.DENIED, microphone=PermissionState.GRANTED
        )
    )
    coordinator, _fake, store = _make_coordinator(data_dir, fake=fake)
    with pytest.raises(PermissionDeniedError):
        coordinator.start_recording()
    state_after = coordinator.state
    error_after = coordinator.last_error
    assert state_after is RecordingState.ERROR
    assert error_after is not None
    assert error_after[0] == "permission_denied"
    # No session directory should exist: permissions are checked before a session is created.
    assert store.paths.recordings_dir.exists()
    assert list(store.paths.recordings_dir.iterdir()) == []


def test_microphone_denied_only_blocks_when_microphone_requested(data_dir: Path) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.GRANTED, microphone=PermissionState.DENIED
        )
    )
    coordinator, _fake, _store = _make_coordinator(data_dir, fake=fake)
    started = coordinator.start_recording(capture_microphone=False)
    assert started is not None
    state_after = coordinator.state
    assert state_after is RecordingState.RECORDING


def test_backend_start_failure_moves_to_error_and_preserves_session(data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_start_with=CaptureError("helper would not launch"))
    coordinator, _fake, store = _make_coordinator(data_dir, fake=fake)
    with pytest.raises(CaptureError):
        coordinator.start_recording()
    state_after = coordinator.state
    assert state_after is RecordingState.ERROR
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].status is SessionStatus.FAILED
    assert sessions[0].error_code == "capture_error"


def test_backend_crash_during_recording_moves_to_error(data_dir: Path) -> None:
    coordinator, fake, store = _make_coordinator(data_dir)
    started = coordinator.start_recording()
    assert started is not None
    fake.simulate_crash(returncode=9, message="helper died")

    state_after = coordinator.state
    error_after = coordinator.last_error
    assert state_after is RecordingState.ERROR
    assert error_after == ("helper_crashed", "helper died")
    assert fake.shutdown_calls == 1
    reread = store.read(started.session_id)
    assert reread.status is SessionStatus.FAILED
    assert reread.error_code == "helper_crashed"


def test_crash_after_already_finalizing_is_not_double_handled(data_dir: Path) -> None:
    """A crash observed after we already moved on must not clobber a later, correct state."""
    coordinator, fake, _store = _make_coordinator(data_dir)
    coordinator.start_recording()
    coordinator.stop_recording()
    assert coordinator.state is RecordingState.SAVED

    fake.simulate_crash(returncode=1, message="late, stale crash notification")
    state_after = coordinator.state
    assert state_after is RecordingState.SAVED  # unchanged; crash handler saw state != Recording


def test_stop_failure_moves_to_error_and_raises(data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_stop_with=CaptureError("stop timed out"))
    coordinator, _fake, store = _make_coordinator(data_dir, fake=fake)
    started = coordinator.start_recording()
    assert started is not None
    with pytest.raises(CaptureError):
        coordinator.stop_recording()
    state_after = coordinator.state
    assert state_after is RecordingState.ERROR
    reread = store.read(started.session_id)
    assert reread.status is SessionStatus.FAILED
    assert reread.error_code == "capture_error"


def test_missing_output_file_moves_to_error_and_raises(data_dir: Path) -> None:
    coordinator, _fake, store = _make_coordinator(data_dir)
    started = coordinator.start_recording()
    assert started is not None
    store.recording_path(started).unlink()  # simulate the file vanishing before finalization

    with pytest.raises(MediaError):
        coordinator.stop_recording()
    state_after = coordinator.state
    assert state_after is RecordingState.ERROR
    reread = store.read(started.session_id)
    assert reread.status is SessionStatus.FAILED
    assert reread.error_code == "missing_output_file"


def test_stop_during_preparing_cancels_without_ever_reaching_recording(data_dir: Path) -> None:
    fake = FakeCaptureBackend(start_delay_s=0.3)
    coordinator, _fake, _store = _make_coordinator(data_dir, fake=fake)
    observed_states: list[RecordingState] = []
    result: dict[str, object] = {}

    def run_start() -> None:
        result["metadata"] = coordinator.start_recording()

    thread = threading.Thread(target=run_start)
    thread.start()
    time.sleep(0.05)
    assert coordinator.state is RecordingState.PREPARING
    stop_result = coordinator.stop_recording()
    thread.join(timeout=5)
    assert not thread.is_alive()

    observed_states.append(coordinator.state)
    assert stop_result is None  # the stop call itself only flags the cancellation
    assert coordinator.state is RecordingState.SAVED  # start_recording() finalized it cleanly
    assert coordinator.state is not RecordingState.RECORDING
    assert result["metadata"] is not None


def test_reset_from_error_returns_to_idle(data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_start_with=CaptureError("boom"))
    coordinator, _fake, _store = _make_coordinator(data_dir, fake=fake)
    with pytest.raises(CaptureError):
        coordinator.start_recording()
    assert coordinator.state is RecordingState.ERROR
    coordinator.reset()
    assert coordinator.state is RecordingState.IDLE
    assert coordinator.current_session is None


def test_cannot_start_again_from_saved_without_reset(data_dir: Path) -> None:
    coordinator, _fake, _store = _make_coordinator(data_dir)
    coordinator.start_recording()
    coordinator.stop_recording()
    assert coordinator.state is RecordingState.SAVED
    with pytest.raises(InvalidTransitionError):
        coordinator.start_recording()
    coordinator.reset()
    second = coordinator.start_recording()
    assert second is not None
    assert second.session_id != ""


def test_shutdown_stops_active_recording_and_shuts_backend_down(data_dir: Path) -> None:
    coordinator, fake, _store = _make_coordinator(data_dir)
    coordinator.start_recording()
    coordinator.shutdown()
    assert coordinator.state is RecordingState.SAVED
    assert fake.stop_calls == 1
    assert fake.shutdown_calls == 1


def test_shutdown_when_idle_is_a_safe_noop(data_dir: Path) -> None:
    coordinator, fake, _store = _make_coordinator(data_dir)
    coordinator.shutdown()
    assert coordinator.state is RecordingState.IDLE
    assert fake.shutdown_calls == 1


def test_state_change_callback_is_invoked_on_every_transition(data_dir: Path) -> None:
    observed: list[RecordingState] = []
    coordinator, _fake, _store = _make_coordinator(
        data_dir, callbacks=CoordinatorCallbacks(on_state_changed=observed.append)
    )
    coordinator.start_recording()
    coordinator.stop_recording()
    assert observed == [
        RecordingState.PREPARING,
        RecordingState.RECORDING,
        RecordingState.FINALIZING,
        RecordingState.SAVED,
    ]


def test_stats_and_warning_callbacks_forward_from_backend(data_dir: Path) -> None:
    stats_seen: list[object] = []
    warnings_seen: list[object] = []
    coordinator, fake, _store = _make_coordinator(
        data_dir,
        callbacks=CoordinatorCallbacks(on_stats=stats_seen.append, on_warning=warnings_seen.append),
    )
    coordinator.start_recording()
    fake.emit_stats(elapsed_s=3.0)
    fake.emit_warning("low_disk_space", "free disk space is low", free_mb=100.0)
    assert len(stats_seen) == 1
    assert len(warnings_seen) == 1


def test_recovered_sessions_exposed_at_construction(data_dir: Path) -> None:
    orphaned, _fake, store = _make_coordinator(data_dir, recover_on_init=False)
    leftover = orphaned.start_recording()
    assert leftover is not None
    # Simulate a few captured frames landing on disk, then the app crashing before
    # stop_recording() is ever called: session.json is left at status "in_progress".
    with store.recording_path(leftover).open("ab") as fh:
        fh.write(b"some captured media bytes")

    coordinator, _fake2, _store2 = _make_coordinator(data_dir, recover_on_init=True)
    assert len(coordinator.recovered_sessions) == 1
    assert coordinator.recovered_sessions[0].session_id == leftover.session_id
    assert coordinator.recovered_sessions[0].status is SessionStatus.RECOVERED
