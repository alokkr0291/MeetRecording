"""Picks the right ``CaptureBackend`` implementation for the current platform."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from local_meeting_recorder.capture.macos.backend import MacOSCaptureBackend
from local_meeting_recorder.capture.protocols import CaptureBackend, CaptureCallbacks
from local_meeting_recorder.capture.windows.backend import WindowsCaptureBackend
from local_meeting_recorder.domain.errors import HelperUnavailableError
from local_meeting_recorder.storage.paths import platform_name

# capture.macos / capture.windows are pure Python (they only shell out to a compiled
# native binary at runtime, resolved lazily), so importing both here unconditionally is
# safe on any platform; only *constructing* a given backend assumes its platform.


def default_backend_factory(
    *, helper_path: Path | None = None, helper_log_path: Path | None = None
) -> Callable[[CaptureCallbacks], CaptureBackend]:
    """A ``backend_factory`` for ``RecordingCoordinator``, chosen by ``sys.platform``.

    Compares ``platform_name()`` (a function, see its docstring) rather than ``sys.platform``
    directly — mypy statically narrows a literal ``sys.platform == "darwin"`` comparison and
    would otherwise flag every other branch here as unreachable on a Mac.
    """
    platform = platform_name()
    if platform == "darwin":

        def factory(callbacks: CaptureCallbacks) -> CaptureBackend:
            return MacOSCaptureBackend(
                helper_path=helper_path, callbacks=callbacks, helper_log_path=helper_log_path
            )

        return factory

    if platform == "win32":

        def factory(callbacks: CaptureCallbacks) -> CaptureBackend:
            return WindowsCaptureBackend(
                helper_path=helper_path, callbacks=callbacks, helper_log_path=helper_log_path
            )

        return factory

    raise HelperUnavailableError(f"no capture backend is implemented yet for platform {platform!r}")
