"""Picks the right ``CaptureBackend`` implementation for the current platform.

Adding Windows support later (Phase 7) means adding a branch here and a
``capture/windows/backend.py`` implementing ``CaptureBackend`` — nothing above this
module (``application/``, ``ui/``) needs to change.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from local_meeting_recorder.capture.macos.backend import MacOSCaptureBackend
from local_meeting_recorder.capture.protocols import CaptureBackend, CaptureCallbacks
from local_meeting_recorder.domain.errors import HelperUnavailableError

# capture.macos is pure Python (it only shells out to the compiled Swift binary at
# runtime, resolved lazily), so importing it here unconditionally is safe on any
# platform; only *constructing* MacOSCaptureBackend assumes darwin.


def default_backend_factory(
    *, helper_path: Path | None = None, helper_log_path: Path | None = None
) -> Callable[[CaptureCallbacks], CaptureBackend]:
    """A ``backend_factory`` for ``RecordingCoordinator``, chosen by ``sys.platform``."""
    if sys.platform == "darwin":

        def factory(callbacks: CaptureCallbacks) -> CaptureBackend:
            return MacOSCaptureBackend(
                helper_path=helper_path, callbacks=callbacks, helper_log_path=helper_log_path
            )

        return factory

    raise HelperUnavailableError(
        f"no capture backend is implemented yet for platform {sys.platform!r} (Windows is Phase 7)"
    )
