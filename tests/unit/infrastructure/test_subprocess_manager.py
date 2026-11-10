from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from local_meeting_recorder.infrastructure.subprocess_manager import ManagedProcess

ECHO_SCRIPT = """
import sys
for line in sys.stdin:
    line = line.strip()
    if line == "quit":
        break
    sys.stdout.write("echo:" + line + "\\n"); sys.stdout.flush()
    sys.stderr.write("err:" + line + "\\n"); sys.stderr.flush()
"""

IGNORE_EOF_SCRIPT = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write("ready\\n"); sys.stdout.flush()
sys.stdin.read()
while True:
    time.sleep(0.1)
"""

CRASH_SCRIPT = """
import sys, time
sys.stdout.write("ready\\n"); sys.stdout.flush()
time.sleep(0.2)
sys.exit(7)
"""


def _argv(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


def test_echo_roundtrip_and_stderr_redirect(tmp_path: Path) -> None:
    lines: list[str] = []
    got = threading.Event()
    exits: list[int] = []

    def on_line(line: str) -> None:
        lines.append(line)
        got.set()

    proc = ManagedProcess(
        _argv(ECHO_SCRIPT), on_line=on_line, on_exit=exits.append, stderr_path=tmp_path / "err.log"
    )
    proc.start()
    assert proc.is_running
    proc.send_line("hello")
    assert got.wait(5)
    proc.send_line("quit")
    code = proc.stop()
    assert code == 0
    assert exits == [0]
    assert lines == ["echo:hello"]
    assert "err:hello" in (tmp_path / "err.log").read_text()
    assert not proc.is_running


def test_stop_escalates_to_sigkill_when_process_ignores_eof_and_term() -> None:
    ready = threading.Event()
    proc = ManagedProcess(_argv(IGNORE_EOF_SCRIPT), on_line=lambda _l: ready.set())
    proc.start()
    assert ready.wait(5)
    t0 = time.monotonic()
    code = proc.stop(eof_grace_s=0.3, term_grace_s=0.3)
    assert time.monotonic() - t0 < 5
    assert code is not None and code != 0
    assert not proc.is_running


def test_unexpected_exit_is_reported_once() -> None:
    exits: list[int] = []
    done = threading.Event()

    def on_exit(code: int) -> None:
        exits.append(code)
        done.set()

    proc = ManagedProcess(_argv(CRASH_SCRIPT), on_line=lambda _l: None, on_exit=on_exit)
    proc.start()
    assert done.wait(5)
    assert exits == [7]
    with pytest.raises(BrokenPipeError):
        proc.send_line("anything")
    assert proc.stop() == 7
    assert exits == [7]


def test_start_twice_and_missing_binary() -> None:
    proc = ManagedProcess(_argv(ECHO_SCRIPT), on_line=lambda _l: None)
    proc.start()
    with pytest.raises(RuntimeError):
        proc.start()
    proc.send_line("quit")
    proc.stop()

    missing = ManagedProcess(["/nonexistent/binary-xyz"], on_line=lambda _l: None)
    with pytest.raises(OSError):
        missing.start()
    assert missing.stop() is None
