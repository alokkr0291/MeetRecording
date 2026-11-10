"""HelperClient against a fake in-process "helper" (see fake_helper_script.py).

No macOS APIs and no real Swift binary involved — these are unit tests, not the hardware
acceptance test (that lives in tests/capture/test_real_capture.py, gated by LMR_HW_TESTS).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from local_meeting_recorder.capture.macos.helper_client import HelperClient
from local_meeting_recorder.capture.protocols import CaptureCallbacks, StartOptions
from local_meeting_recorder.domain.errors import (
    CaptureError,
    HelperCrashedError,
    HelperProtocolError,
    HelperUnavailableError,
)
from tests.unit.capture.fake_helper_script import FAKE_HELPER_SOURCE


def _make_client(
    *, callbacks: CaptureCallbacks | None = None, stderr_path: Path | None = None
) -> HelperClient:
    return HelperClient(
        Path(sys.executable),
        callbacks=callbacks,
        stderr_path=stderr_path,
        extra_args=["-u", "-c", FAKE_HELPER_SOURCE],
    )


def test_startup_and_shutdown() -> None:
    client = _make_client()
    ready = client.start(timeout=5)
    assert ready.version == "0.0.0-fake"
    running_after_start = client.is_running
    assert running_after_start
    client.shutdown(timeout=2)
    running_after_shutdown = client.is_running
    assert not running_after_shutdown
    # Idempotent.
    client.shutdown(timeout=2)


def test_probe_response() -> None:
    client = _make_client()
    client.start(timeout=5)
    try:
        info = client.probe(timeout=5)
        assert info.permissions.all_granted
    finally:
        client.shutdown(timeout=2)


def test_display_enumeration() -> None:
    client = _make_client()
    client.start(timeout=5)
    try:
        info = client.list_displays(timeout=5)
        assert [d.id for d in info.displays] == [1]
        assert info.displays[0].is_main
    finally:
        client.shutdown(timeout=2)


def test_start_and_stop_recording(tmp_path: Path) -> None:
    stats: list[object] = []
    client = _make_client(callbacks=CaptureCallbacks(on_stats=stats.append))
    client.start(timeout=5)
    try:
        output = tmp_path / "recording.mov"
        options = StartOptions(output_path=output, capture_microphone=True)
        started = client.start_recording(options, timeout=5)
        recording_after_start = client.is_recording
        assert started.output_path == str(output)
        assert recording_after_start
        stopped = client.stop_recording(timeout=5)
        recording_after_stop = client.is_recording
        assert stopped.finalized
        assert not recording_after_stop
    finally:
        client.shutdown(timeout=2)


def test_stop_without_start_raises(tmp_path: Path) -> None:
    client = _make_client()
    client.start(timeout=5)
    try:
        with pytest.raises(CaptureError):
            client.stop_recording(timeout=2)
    finally:
        client.shutdown(timeout=2)


def test_invalid_json_from_helper_is_ignored_not_fatal() -> None:
    client = _make_client()
    client.start(timeout=5)
    try:
        client._send({"cmd": "garbage"})
        # The client must not have crashed or wedged: a normal command still works.
        info = client.probe(timeout=5)
        assert info.permissions.all_granted
    finally:
        client.shutdown(timeout=2)


def test_helper_crash_while_command_pending_raises_crashed_error() -> None:
    client = _make_client()
    client.start(timeout=5)
    with pytest.raises(HelperCrashedError):
        client._send({"cmd": "crash"})
        client._await_event(frozenset({"probe"}), timeout=5)
    client.shutdown(timeout=2)


def test_unexpected_exit_while_idle_invokes_on_crash_callback() -> None:
    crashes: list[tuple[int | None, str]] = []
    done = threading.Event()

    def on_crash(code: int | None, message: str) -> None:
        crashes.append((code, message))
        done.set()

    client = _make_client(callbacks=CaptureCallbacks(on_crash=on_crash))
    client.start(timeout=5)
    client._send({"cmd": "quit"})
    assert done.wait(5)
    assert crashes == [(0, "helper process exited unexpectedly")]
    client.shutdown(timeout=2)


def test_probe_timeout_raises_and_cleanup_still_works() -> None:
    client = _make_client()
    client.start(timeout=5)
    client._send({"cmd": "hang"})
    with pytest.raises(HelperProtocolError):
        client._await_event(frozenset({"probe"}), timeout=0.3)
    # The helper is stuck in time.sleep(); shutdown must still terminate it via SIGTERM.
    client.shutdown(timeout=1)
    assert not client.is_running


def test_shutdown_escalates_to_sigkill_when_helper_ignores_sigterm() -> None:
    client = _make_client()
    client.start(timeout=5)
    client._send({"cmd": "hang-ignore-term"})
    client.shutdown(timeout=0.3)
    assert not client.is_running


def test_missing_binary_raises_helper_unavailable() -> None:
    client = HelperClient(Path("/nonexistent/lmr-capture-helper"))
    with pytest.raises(HelperUnavailableError):
        client.start(timeout=2)
