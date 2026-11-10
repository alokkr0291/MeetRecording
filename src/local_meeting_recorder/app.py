"""Composition root: builds the coordinator, bridge, tray, and controller, then runs Qt.

Nothing here decides *how* recording works (``application/``) or *what* the UI looks
like (``ui/``) — this module only wires the pieces together and owns the Qt event loop.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtWidgets import QApplication

from local_meeting_recorder.application.recording_coordinator import RecordingCoordinator
from local_meeting_recorder.capture.protocols import CaptureBackend, CaptureCallbacks
from local_meeting_recorder.config import Settings
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.ui.app_controller import AppController
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.tray import TrayController

log = get_logger(__name__)


class Application:
    """Owns the ``QApplication`` and every long-lived object. ``run()`` blocks until quit."""

    def __init__(
        self,
        *,
        backend_factory: Callable[[CaptureCallbacks], CaptureBackend],
        session_store: SessionStore,
        settings: Settings,
    ) -> None:
        existing = QApplication.instance()
        self.qt_app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
        # A tray-only app has no main window; without this, closing a transient dialog
        # (consent, permission guide) would be mistaken for "the last window closed" and
        # quit the whole application.
        self.qt_app.setQuitOnLastWindowClosed(False)

        self.bridge = CoordinatorBridge()
        self.coordinator = RecordingCoordinator(
            backend_factory=backend_factory,
            session_store=session_store,
            settings=settings,
            callbacks=self.bridge.callbacks,
        )
        self.bridge.attach(self.coordinator)

        self.tray = TrayController()
        self.controller = AppController(
            coordinator=self.coordinator,
            bridge=self.bridge,
            tray=self.tray,
            session_store=session_store,
            settings=settings,
        )

        self.qt_app.aboutToQuit.connect(self._on_about_to_quit)

    def run(self) -> int:
        self.tray.show()
        self.controller.announce_recovered_sessions()
        return self.qt_app.exec()

    def _on_about_to_quit(self) -> None:
        log.info("application shutting down")
        self.coordinator.shutdown()
