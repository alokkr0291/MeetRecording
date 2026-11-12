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
from local_meeting_recorder.infrastructure.credentials.factory import default_credential_store
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.settings.profiles import effects_for
from local_meeting_recorder.settings.service import (
    SETTINGS_FILE_NAME,
    SettingsService,
    apply_recordings_dir_override,
)
from local_meeting_recorder.speaker.factory import build_diarization_engine
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.summarization.manager import SummaryManager
from local_meeting_recorder.summarization.providers.factory import build_summary_client
from local_meeting_recorder.transcription.manager import TranscriptionManager
from local_meeting_recorder.transcription.providers.factory import build_provider_client
from local_meeting_recorder.ui.app_controller import AppController
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.settings_dialog import SettingsDialogContext
from local_meeting_recorder.ui.summary_bridge import SummaryBridge
from local_meeting_recorder.ui.transcription_bridge import TranscriptionBridge
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
        settings_service: SettingsService | None = None,
        credential_store: CredentialStore | None = None,
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

        self.settings_service = settings_service or SettingsService(
            session_store.paths.root / SETTINGS_FILE_NAME
        )
        self.credential_store = credential_store or default_credential_store()
        recordings_dir_error = apply_recordings_dir_override(
            session_store.paths, self.settings_service.current
        )

        # Phase 6: one app-wide limiter shared by transcription, diarization, and
        # summarization — not per-session. Initialized from whatever processing profile
        # was already saved; SettingsDialog updates its live limit when the user changes
        # the profile (see ui/settings_dialog.py).
        self.resource_governor = ResourceGovernor(
            max_concurrent_ai_jobs=effects_for(
                self.settings_service.current.processing_profile
            ).max_concurrent_ai_jobs
        )

        self.transcription_bridge = TranscriptionBridge()
        self.transcription_manager = TranscriptionManager(
            session_store=session_store,
            settings=settings,
            callbacks=self.transcription_bridge.callbacks,
            engine_factory=lambda: build_provider_client(
                app_settings=self.settings_service.current,
                dev_settings=settings,
                credential_store=self.credential_store,
            ),
            diarization_engine_factory=lambda: build_diarization_engine(
                self.credential_store,
                cpu_threads=effects_for(
                    self.settings_service.current.processing_profile
                ).cpu_threads,
            ),
            resource_governor=self.resource_governor,
        )

        self.summary_bridge = SummaryBridge()
        self.summary_manager = SummaryManager(
            session_store=session_store,
            callbacks=self.summary_bridge.callbacks,
            client_factory=lambda: build_summary_client(
                app_settings=self.settings_service.current,
                credential_store=self.credential_store,
            ),
            resource_governor=self.resource_governor,
        )

        self.tray = TrayController()
        settings_dialog_context = SettingsDialogContext(
            settings_service=self.settings_service,
            credential_store=self.credential_store,
            storage_paths=session_store.paths,
            transcription_manager=self.transcription_manager,
            resource_governor=self.resource_governor,
        )
        self.controller = AppController(
            coordinator=self.coordinator,
            bridge=self.bridge,
            tray=self.tray,
            session_store=session_store,
            settings=settings,
            transcription_manager=self.transcription_manager,
            transcription_bridge=self.transcription_bridge,
            settings_dialog_context=settings_dialog_context,
            settings_service=self.settings_service,
            summary_manager=self.summary_manager,
            summary_bridge=self.summary_bridge,
        )
        if recordings_dir_error is not None:
            self.tray.show_notification("Recording Folder Unavailable", recordings_dir_error)

        self.qt_app.aboutToQuit.connect(self._on_about_to_quit)

    def run(self) -> int:
        self.tray.show()
        self.controller.announce_recovered_sessions()
        return self.qt_app.exec()

    def _on_about_to_quit(self) -> None:
        log.info("application shutting down")
        self.coordinator.shutdown()
