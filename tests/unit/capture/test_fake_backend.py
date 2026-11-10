"""FakeCaptureBackend's own behaviour, and that it satisfies the CaptureBackend protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_recorder.capture.fake import FakeCaptureBackend
from local_meeting_recorder.capture.protocols import CaptureBackend, CaptureCallbacks, StartOptions
from local_meeting_recorder.domain.errors import CaptureError


def test_satisfies_capture_backend_protocol() -> None:
    backend: CaptureBackend = FakeCaptureBackend()
    assert not backend.is_recording


def test_start_then_stop_round_trip(tmp_path: Path) -> None:
    backend = FakeCaptureBackend()
    options = StartOptions(output_path=tmp_path / "recording.mov")
    started = backend.start(options)
    recording_after_start = backend.is_recording
    assert recording_after_start
    assert started.output_path == str(options.output_path)
    assert [t.role for t in started.tracks if t.kind == "audio"] == ["system", "microphone"]

    stopped = backend.stop()
    recording_after_stop = backend.is_recording
    assert stopped.finalized
    assert not recording_after_stop
    assert backend.start_calls == [options]
    assert backend.stop_calls == 1


def test_start_twice_raises() -> None:
    backend = FakeCaptureBackend()
    backend.start(StartOptions(output_path=Path("/tmp/a.mov")))
    with pytest.raises(CaptureError):
        backend.start(StartOptions(output_path=Path("/tmp/b.mov")))


def test_stop_without_start_raises() -> None:
    backend = FakeCaptureBackend()
    with pytest.raises(CaptureError):
        backend.stop()


def test_scripted_start_failure() -> None:
    backend = FakeCaptureBackend(fail_start_with=CaptureError("permission denied"))
    with pytest.raises(CaptureError):
        backend.start(StartOptions(output_path=Path("/tmp/a.mov")))
    assert not backend.is_recording


def test_scripted_stop_failure_still_clears_recording_state() -> None:
    backend = FakeCaptureBackend(fail_stop_with=CaptureError("writer failed"))
    backend.start(StartOptions(output_path=Path("/tmp/a.mov")))
    with pytest.raises(CaptureError):
        backend.stop()
    assert not backend.is_recording


def test_emit_stats_and_warning_invoke_callbacks() -> None:
    stats: list[object] = []
    warnings: list[object] = []
    backend = FakeCaptureBackend(
        callbacks=CaptureCallbacks(on_stats=stats.append, on_warning=warnings.append)
    )
    backend.emit_stats(elapsed_s=2.5)
    backend.emit_warning("low_disk_space", "free disk space is low", free_mb=100.0)
    assert len(stats) == 1
    assert len(warnings) == 1


def test_simulate_crash_invokes_callback_and_clears_recording_state() -> None:
    crashes: list[tuple[int | None, str]] = []
    backend = FakeCaptureBackend(
        callbacks=CaptureCallbacks(on_crash=lambda code, message: crashes.append((code, message)))
    )
    backend.start(StartOptions(output_path=Path("/tmp/a.mov")))
    backend.simulate_crash(returncode=9)
    assert not backend.is_recording
    assert crashes == [(9, "simulated crash")]


def test_shutdown_is_safe_without_an_active_recording() -> None:
    backend = FakeCaptureBackend()
    backend.shutdown()
    assert backend.shutdown_calls == 1
