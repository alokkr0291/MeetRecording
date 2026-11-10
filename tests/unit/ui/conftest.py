"""Shared fixtures for ui/ tests: one offscreen QApplication for the whole session.

See .claude/skills/testing: unit tests may use a Qt event loop only where unavoidable.
That is exactly CoordinatorBridge's cross-thread signals (background thread -> GUI
thread via a queued connection) — ``pump_until`` below processes events just long enough
to let those arrive; same-thread signals (tray -> controller) fire synchronously and
need no pumping at all.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    return app


def pump_until(condition: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()
