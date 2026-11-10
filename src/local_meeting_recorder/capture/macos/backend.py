"""Real macOS capture backend: one short-lived ``HelperClient`` per operation.

The helper exits on its own once a recording is stopped (see ``helper_client`` module
docstring), so this class never tries to reuse a client across a recording. A permission
check or display listing uses its own throwaway client too, so a stale probe never shares
state with whatever happens next.
"""

from __future__ import annotations

from pathlib import Path

from local_meeting_recorder.capture.macos.helper_client import HelperClient
from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    DisplayInfo,
    PermissionsRequestResult,
    PermissionStatus,
    StartedInfo,
    StartOptions,
    StoppedInfo,
)
from local_meeting_recorder.domain.errors import CaptureError, HelperUnavailableError
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.paths import resolve_helper_binary

log = get_logger(__name__)


class MacOSCaptureBackend:
    """Implements ``capture.protocols.CaptureBackend`` for macOS."""

    def __init__(
        self,
        *,
        helper_path: Path | None = None,
        callbacks: CaptureCallbacks | None = None,
        helper_log_path: Path | None = None,
    ) -> None:
        resolved = helper_path or resolve_helper_binary()
        if resolved is None:
            raise HelperUnavailableError(
                "lmr-capture-helper binary not found; run scripts/build_helper.sh "
                "or set LMR_HELPER_PATH"
            )
        self._helper_path = resolved
        self._callbacks = callbacks or CaptureCallbacks()
        self._helper_log_path = helper_log_path
        self._active_client: HelperClient | None = None

    @property
    def is_recording(self) -> bool:
        return self._active_client is not None and self._active_client.is_recording

    def _new_client(self, *, stderr_path: Path | None = None) -> HelperClient:
        return HelperClient(
            self._helper_path,
            callbacks=self._callbacks,
            stderr_path=stderr_path if stderr_path is not None else self._helper_log_path,
        )

    def check_permissions(self, *, timeout: float = 5.0) -> PermissionStatus:
        client = self._new_client()
        try:
            client.start(timeout=timeout)
            info = client.probe(timeout=timeout)
            return info.permissions
        finally:
            client.shutdown(timeout=timeout)

    def request_permissions(self, *, timeout: float = 30.0) -> PermissionsRequestResult:
        client = self._new_client()
        try:
            client.start(timeout=min(timeout, 10.0))
            return client.request_permissions(timeout=timeout)
        finally:
            client.shutdown(timeout=min(timeout, 10.0))

    def list_displays(self, *, timeout: float = 5.0) -> list[DisplayInfo]:
        client = self._new_client()
        try:
            client.start(timeout=timeout)
            info = client.list_displays(timeout=timeout)
            return list(info.displays)
        finally:
            client.shutdown(timeout=timeout)

    def start(self, options: StartOptions, *, timeout: float = 20.0) -> StartedInfo:
        if self.is_recording:
            raise CaptureError("a recording is already in progress")
        client = self._new_client()
        try:
            client.start(timeout=min(timeout, 10.0))
            started = client.start_recording(options, timeout=timeout)
        except Exception:
            client.shutdown(timeout=5.0)
            raise
        self._active_client = client
        return started

    def stop(self, *, timeout: float = 30.0) -> StoppedInfo:
        client = self._active_client
        if client is None:
            raise CaptureError("stop() called with no active recording")
        try:
            stopped = client.stop_recording(timeout=timeout)
        finally:
            client.shutdown(timeout=min(timeout, 10.0))
            self._active_client = None
        return stopped

    def shutdown(self) -> None:
        """Best-effort cleanup for app exit: stop any active recording, then let go."""
        client, self._active_client = self._active_client, None
        if client is None:
            return
        if client.is_recording:
            try:
                client.stop_recording(timeout=10.0)
            except CaptureError:
                log.warning("shutdown: stop_recording failed; forcing process termination")
        client.shutdown(timeout=10.0)
