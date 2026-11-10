"""Bounded lifecycle for line-oriented child processes.

Every subprocess the application starts goes through ``ManagedProcess`` so that:

* stdout is drained by a dedicated thread (no pipe deadlocks);
* exit is detected promptly and reported through a callback;
* shutdown is deterministic: close stdin (EOF), wait, SIGTERM, wait, SIGKILL, wait, join threads.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from local_meeting_recorder.infrastructure.logging_setup import get_logger

log = get_logger(__name__)

LineCallback = Callable[[str], None]
ExitCallback = Callable[[int], None]


class ManagedProcess:
    """Owns one child process that speaks a line protocol on stdin/stdout.

    ``stderr`` is redirected to ``stderr_path`` when given (the helper log), otherwise inherited.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        on_line: LineCallback,
        on_exit: ExitCallback | None = None,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stderr_path: Path | None = None,
        name: str | None = None,
    ) -> None:
        self._argv = list(argv)
        self._on_line = on_line
        self._on_exit = on_exit
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._stderr_path = stderr_path
        self._name = name or Path(self._argv[0]).name
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_file: IO[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._waiter: threading.Thread | None = None
        self._stdin_lock = threading.Lock()
        self._exit_reported = threading.Event()
        self._stopping = False

    # -- properties -------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def stopping(self) -> bool:
        return self._stopping

    # -- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError(f"{self._name}: already started")
        stderr_target: IO[bytes] | None = None  # None: inherit the parent's stderr
        if self._stderr_path is not None:
            self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_file = self._stderr_path.open("ab")
            stderr_target = self._stderr_file
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                cwd=str(self._cwd) if self._cwd else None,
                env=self._env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,  # own process group: our signals never hit the parent
            )
        except OSError:
            self._close_stderr()
            raise
        log.info("subprocess started", extra={"proc": self._name, "pid": self._proc.pid})
        self._reader = threading.Thread(
            target=self._read_stdout, name=f"{self._name}-stdout", daemon=True
        )
        self._reader.start()
        self._waiter = threading.Thread(
            target=self._wait_for_exit, name=f"{self._name}-wait", daemon=True
        )
        self._waiter.start()

    def send_line(self, line: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError(f"{self._name}: not started")
        if proc.poll() is not None:
            raise BrokenPipeError(f"{self._name}: process already exited ({proc.returncode})")
        with self._stdin_lock:
            proc.stdin.write(line.rstrip("\n") + "\n")
            proc.stdin.flush()

    def close_stdin(self) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        with self._stdin_lock, contextlib.suppress(OSError):
            proc.stdin.close()

    def stop(self, *, eof_grace_s: float = 5.0, term_grace_s: float = 5.0) -> int | None:
        """Shut the process down in escalating steps and wait for all threads.

        Returns the exit code (None if the process never started).
        """
        proc = self._proc
        if proc is None:
            return None
        self._stopping = True
        if proc.poll() is None:
            self.close_stdin()
            if not self._wait(proc, eof_grace_s):
                log.warning(
                    "subprocess ignored stdin EOF, sending SIGTERM",
                    extra={"proc": self._name, "pid": proc.pid},
                )
                self._signal(proc, signal.SIGTERM)
                if not self._wait(proc, term_grace_s):
                    log.error(
                        "subprocess ignored SIGTERM, sending SIGKILL",
                        extra={"proc": self._name, "pid": proc.pid},
                    )
                    self._signal(proc, signal.SIGKILL)
                    proc.wait()
        self.join(timeout=5.0)
        return proc.returncode

    def join(self, timeout: float | None = None) -> None:
        for thread in (self._reader, self._waiter):
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout)
        self._close_stderr()

    def wait(self, timeout: float | None = None) -> int | None:
        proc = self._proc
        if proc is None:
            return None
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    # -- internals --------------------------------------------------------------------

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                try:
                    self._on_line(line)
                except Exception:  # a bad callback must not kill the reader
                    log.exception("on_line callback failed", extra={"proc": self._name})
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                proc.stdout.close()

    def _wait_for_exit(self) -> None:
        proc = self._proc
        assert proc is not None
        code = proc.wait()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        log.info(
            "subprocess exited",
            extra={
                "proc": self._name,
                "pid": proc.pid,
                "returncode": code,
                "stopping": self._stopping,
            },
        )
        if not self._exit_reported.is_set():
            self._exit_reported.set()
            if self._on_exit is not None:
                try:
                    self._on_exit(code)
                except Exception:
                    log.exception("on_exit callback failed", extra={"proc": self._name})

    @staticmethod
    def _wait(proc: subprocess.Popen[str], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.05)
        return proc.poll() is not None

    @staticmethod
    def _signal(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            proc.send_signal(sig)

    def _close_stderr(self) -> None:
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            finally:
                self._stderr_file = None
