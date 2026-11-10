"""A simple, POSIX-only single-instance guard using an exclusive file lock.

Windows needs its own mechanism (e.g. a named mutex) once Phase 7 adds that platform;
this module is intentionally small and isolated so swapping it out later touches nothing
else. On Windows this is a no-op for now rather than a hard failure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType
from typing import IO

if sys.platform != "win32":
    import fcntl

from local_meeting_recorder.domain.errors import LMRError


class AlreadyRunningError(LMRError):
    code = "already_running"


class SingleInstanceGuard:
    """Context manager: raises ``AlreadyRunningError`` if another instance holds the lock."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fh: IO[str] | None = None

    def __enter__(self) -> SingleInstanceGuard:
        if sys.platform == "win32":
            return self  # Phase 7: a native Windows guard.

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = self._lock_path.open("w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunningError(
                f"another instance holds the lock at {self._lock_path}"
            ) from exc
        self._fh = fh
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is None:
            return
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None
