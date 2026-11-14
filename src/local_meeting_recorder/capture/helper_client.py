"""Drives one ``lmr-capture-helper`` process over the JSON-lines protocol.

Platform-neutral: this class only knows how to speak the JSON-lines wire protocol to
*some* executable via ``infrastructure.subprocess_manager.ManagedProcess`` — it has no
macOS- or Windows-specific logic at all. Both ``capture.macos.backend.MacOSCaptureBackend``
and ``capture.windows.backend.WindowsCaptureBackend`` share this one implementation rather
than each carrying their own copy, since the protocol (``native/macos/PROTOCOL.md``) is
deliberately the same wire format on every platform.

Lifecycle note: the real helper exits on its own once a recording is stopped, so one
``HelperClient`` instance is good for exactly one logical operation: a permission/display
query, or one start-recording-then-stop-recording cycle. Each backend creates a fresh
client per operation; this class does not try to be reusable across a process exit.

Concurrency model: the protocol is request/response and the helper processes commands in
order, so only one command is ever "in flight" from this client at a time. A single
``_Pending`` slot (guarded by a lock) is fulfilled by the reader thread and awaited by the
caller's thread via a ``threading.Event`` — this is the same non-blocking-I/O-plus-callback
style ``infrastructure.subprocess_manager.ManagedProcess`` already uses, just with typed
responses layered on top. Nothing here blocks the Qt event loop: callers choose their own
timeouts and this class never waits longer than that.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    DisplaysInfo,
    PermissionsRequestResult,
    ProbeInfo,
    ReadyInfo,
    StartedInfo,
    StartOptions,
    StoppedInfo,
    parse_event,
)
from local_meeting_recorder.domain.errors import (
    CaptureError,
    HelperCrashedError,
    HelperProtocolError,
    HelperUnavailableError,
)
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.infrastructure.subprocess_manager import ManagedProcess

log = get_logger(__name__)


class _Pending:
    __slots__ = ("done", "error", "event_names", "model", "name")

    def __init__(self, event_names: frozenset[str]) -> None:
        self.event_names = event_names
        self.done = threading.Event()
        self.name: str | None = None
        self.model: BaseModel | None = None
        self.error: Exception | None = None


class HelperClient:
    def __init__(
        self,
        helper_path: Path,
        *,
        callbacks: CaptureCallbacks | None = None,
        stderr_path: Path | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._helper_path = helper_path
        self._callbacks = callbacks or CaptureCallbacks()
        self._stderr_path = stderr_path
        self._extra_args = list(extra_args)
        self._proc = ManagedProcess(
            [str(helper_path), *self._extra_args],
            on_line=self._on_line,
            on_exit=self._on_exit,
            stderr_path=stderr_path,
            name="lmr-capture-helper",
        )
        self._lock = threading.Lock()
        self._pending: _Pending | None = None
        self._ready: ReadyInfo | None = None
        self._recording = False
        self._expecting_exit = False

    # -- properties ---------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def is_running(self) -> bool:
        return self._proc.is_running

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def ready_info(self) -> ReadyInfo | None:
        return self._ready

    # -- lifecycle ------------------------------------------------------------------------

    def start(self, *, timeout: float = 10.0) -> ReadyInfo:
        """Launch the helper and wait for its ``ready`` event."""
        try:
            self._proc.start()
        except OSError as exc:
            raise HelperUnavailableError(
                f"could not start capture helper at {self._helper_path}: {exc}"
            ) from exc
        _name, model = self._await_event(frozenset({"ready"}), timeout)
        assert isinstance(model, ReadyInfo)
        self._ready = model
        return model

    def probe(self, *, timeout: float = 5.0) -> ProbeInfo:
        self._send({"cmd": "probe"})
        _name, model = self._await_event(frozenset({"probe"}), timeout)
        assert isinstance(model, ProbeInfo)
        return model

    def request_permissions(self, *, timeout: float = 30.0) -> PermissionsRequestResult:
        self._send({"cmd": "request-permissions"})
        _name, model = self._await_event(frozenset({"permissions"}), timeout)
        assert isinstance(model, PermissionsRequestResult)
        return model

    def list_displays(self, *, timeout: float = 5.0) -> DisplaysInfo:
        self._send({"cmd": "list-displays"})
        _name, model = self._await_event(frozenset({"displays"}), timeout)
        assert isinstance(model, DisplaysInfo)
        return model

    def start_recording(self, options: StartOptions, *, timeout: float = 20.0) -> StartedInfo:
        if self._recording:
            raise CaptureError("start_recording called while already recording")
        self._send(options.to_command())
        _name, model = self._await_event(frozenset({"started"}), timeout)
        assert isinstance(model, StartedInfo)
        self._recording = True
        return model

    def stop_recording(self, *, timeout: float = 30.0) -> StoppedInfo:
        if not self._recording:
            raise CaptureError("stop_recording called while not recording")
        self._send({"cmd": "stop"})
        # A clean stop always ends with the helper process exiting on its own.
        self._expecting_exit = True
        try:
            _name, model = self._await_event(frozenset({"stopped"}), timeout)
        finally:
            self._recording = False
        assert isinstance(model, StoppedInfo)
        return model

    def shutdown(self, *, timeout: float = 10.0) -> None:
        """Ensure the process is gone, however it got here. Safe to call more than once."""
        self._expecting_exit = True
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.error = HelperCrashedError(
                "helper client shut down while a command was pending"
            )
            pending.done.set()
        self._proc.stop(eof_grace_s=min(5.0, timeout), term_grace_s=min(5.0, timeout))
        self._recording = False

    # -- internals ------------------------------------------------------------------------

    def _send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        try:
            self._proc.send_line(line)
        except BrokenPipeError as exc:
            raise HelperCrashedError(
                f"helper process is not running: {exc}", returncode=self._proc.returncode
            ) from exc

    def _await_event(self, event_names: frozenset[str], timeout: float) -> tuple[str, BaseModel]:
        pending = _Pending(event_names)
        with self._lock:
            if self._pending is not None:
                raise RuntimeError(
                    "HelperClient: a command is already awaiting a response "
                    "(the protocol is strictly request/response; do not call concurrently)"
                )
            self._pending = pending
        try:
            if not pending.done.wait(timeout):
                raise HelperProtocolError(
                    f"timed out after {timeout}s waiting for one of {sorted(event_names)}"
                )
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None
        if pending.error is not None:
            raise pending.error
        assert pending.name is not None and pending.model is not None
        return pending.name, pending.model

    def _on_line(self, line: str) -> None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.warning("helper sent non-JSON line", extra={"line": line})
            return
        if not isinstance(obj, dict):
            log.warning("helper line is not a JSON object", extra={"line": line})
            return
        try:
            name, model = parse_event(obj)
        except HelperProtocolError:
            log.warning("helper sent an unparseable event", extra={"line": line})
            return
        self._route(name, model)

    def _route(self, name: str, model: BaseModel) -> None:
        with self._lock:
            pending = self._pending
            matches = pending is not None and (name in pending.event_names or name == "error")
            if matches:
                assert pending is not None
                pending.name = name
                pending.model = model
                if name == "error":
                    error_model = model
                    assert hasattr(error_model, "to_exception")
                    pending.error = error_model.to_exception()
                self._pending = None
        if matches:
            pending.done.set()  # type: ignore[union-attr]
            return

        if name == "stats" and self._callbacks.on_stats is not None:
            self._callbacks.on_stats(model)  # type: ignore[arg-type]
        elif name == "warning" and self._callbacks.on_warning is not None:
            self._callbacks.on_warning(model)  # type: ignore[arg-type]
        elif name == "stopping" and self._callbacks.on_stopping is not None:
            self._callbacks.on_stopping(model)  # type: ignore[arg-type]
        elif name == "error":
            log.error("unsolicited helper error", extra={"error": model.model_dump()})
            if self._callbacks.on_crash is not None:
                exc = model.to_exception()  # type: ignore[attr-defined]
                self._callbacks.on_crash(None, str(exc))

    def _on_exit(self, returncode: int) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        was_expected = self._expecting_exit
        self._recording = False
        if pending is not None:
            pending.error = HelperCrashedError(
                f"helper process exited with code {returncode} before responding",
                returncode=returncode,
            )
            pending.done.set()
        elif not was_expected and self._callbacks.on_crash is not None:
            self._callbacks.on_crash(returncode, "helper process exited unexpectedly")
