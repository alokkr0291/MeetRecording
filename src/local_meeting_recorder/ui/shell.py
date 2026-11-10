"""Thin wrappers around macOS's ``open`` for "show me that" actions.

A plain ``subprocess.run(["open", ...])`` is simpler and more predictable than
``QDesktopServices`` for both a folder and a ``x-apple.systempreferences:`` deep link, and
is easy to stub out in tests via the ``runner`` parameter.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from local_meeting_recorder.infrastructure.logging_setup import get_logger

log = get_logger(__name__)

Runner = Callable[[list[str]], None]


def default_runner(argv: list[str]) -> None:
    subprocess.run(argv, check=False)


def open_path(path: Path, *, runner: Runner = default_runner) -> None:
    """Reveal a file or folder in Finder, creating it first if it does not exist yet."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    try:
        runner(["open", str(path)])
    except OSError:
        log.exception("failed to open path in Finder", extra={"path": str(path)})


def open_url(url: str, *, runner: Runner = default_runner) -> None:
    try:
        runner(["open", url])
    except OSError:
        log.exception("failed to open URL", extra={"url": url})
