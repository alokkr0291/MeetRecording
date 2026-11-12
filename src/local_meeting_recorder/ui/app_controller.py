"""Wires ``TrayController`` + ``CoordinatorBridge`` together.

This is the one place that knows the UI *flow* — check permissions, then show consent,
then start; disable invalid actions; surface errors and recovered sessions. No
recording-domain logic lives here: every decision is "when to show what" or "which
coordinator call to make next." ``RecordingCoordinator`` still owns every state
transition, session rule, and persistence decision.

``consent_fn``/``permission_guide_fn`` are injected so tests can drive the full
check-permissions -> consent -> start flow without a real modal dialog.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from local_meeting_recorder.application.recording_coordinator import RecordingCoordinator
from local_meeting_recorder.capture.protocols import PermissionState, PermissionStatus, WarningInfo
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import MISSING_API_KEY_CODE
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import (
    SessionMetadata,
    TranscriptionStatus,
    utc_now,
)
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.settings.service import SettingsService
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.summarization.manager import SummaryManager
from local_meeting_recorder.summarization.store import SummaryStore
from local_meeting_recorder.transcription.manager import TranscriptionManager, TranscriptionProgress
from local_meeting_recorder.transcription.store import TranscriptStore
from local_meeting_recorder.ui.consent_dialog import request_consent
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.permission_guide import show_permission_guide
from local_meeting_recorder.ui.settings_dialog import (
    TAB_SUMMARY,
    TAB_TRANSCRIPTION,
    SettingsDialogContext,
    show_settings_dialog,
)
from local_meeting_recorder.ui.shell import open_path
from local_meeting_recorder.ui.summary_bridge import SummaryBridge
from local_meeting_recorder.ui.summary_dialog import show_summary_dialog
from local_meeting_recorder.ui.transcript_dialog import show_transcript_dialog
from local_meeting_recorder.ui.transcription_bridge import TranscriptionBridge
from local_meeting_recorder.ui.tray import TrayController

log = get_logger(__name__)

ConsentFn = Callable[[], bool]
PermissionGuideFn = Callable[[PermissionStatus], None]
TranscriptDialogFn = Callable[..., None]
SummaryDialogFn = Callable[..., None]
SettingsDialogFn = Callable[..., None]


class AppController(QObject):
    def __init__(
        self,
        *,
        coordinator: RecordingCoordinator,
        bridge: CoordinatorBridge,
        tray: TrayController,
        session_store: SessionStore,
        settings: Settings,
        consent_fn: ConsentFn = request_consent,
        permission_guide_fn: PermissionGuideFn | None = None,
        transcription_manager: TranscriptionManager | None = None,
        transcription_bridge: TranscriptionBridge | None = None,
        transcript_store: TranscriptStore | None = None,
        transcript_dialog_fn: TranscriptDialogFn = show_transcript_dialog,
        settings_dialog_context: SettingsDialogContext | None = None,
        settings_dialog_fn: SettingsDialogFn = show_settings_dialog,
        settings_service: SettingsService | None = None,
        summary_manager: SummaryManager | None = None,
        summary_bridge: SummaryBridge | None = None,
        summary_store: SummaryStore | None = None,
        summary_dialog_fn: SummaryDialogFn = show_summary_dialog,
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._bridge = bridge
        self._tray = tray
        self._store = session_store
        self._settings = settings
        self._consent_fn = consent_fn
        self._permission_guide_fn = permission_guide_fn or show_permission_guide
        self._transcription_manager = transcription_manager
        self._transcript_store = transcript_store or TranscriptStore()
        self._transcript_dialog_fn = transcript_dialog_fn
        self._settings_dialog_context = settings_dialog_context
        self._settings_dialog_fn = settings_dialog_fn
        self._settings_service = settings_service
        self._summary_manager = summary_manager
        self._summary_store = summary_store or SummaryStore()
        self._summary_dialog_fn = summary_dialog_fn
        self._transcription_progress: dict[str, float] = {}
        self._last_notification_settings_tab: str | None = None

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration)

        tray.start_requested.connect(self._on_start_requested)
        tray.stop_requested.connect(self._on_stop_requested)
        tray.open_recordings_requested.connect(self._on_open_recordings_requested)
        tray.recent_session_requested.connect(self._on_recent_session_requested)
        tray.settings_requested.connect(self._on_settings_requested)
        tray.notification_clicked.connect(self._on_notification_clicked)
        tray.quit_requested.connect(self._on_quit_requested)

        bridge.state_changed.connect(self._on_state_changed)
        bridge.operation_failed.connect(self._on_operation_failed)
        bridge.warning_raised.connect(self._on_warning_raised)
        bridge.permissions_checked.connect(self._on_permissions_checked)
        bridge.permissions_requested.connect(self._on_permissions_requested)

        if transcription_bridge is not None:
            transcription_bridge.started.connect(self._on_transcription_started)
            transcription_bridge.progress.connect(self._on_transcription_progress)
            transcription_bridge.completed.connect(self._on_transcription_completed)
            transcription_bridge.failed.connect(self._on_transcription_failed)
            transcription_bridge.diarization_started.connect(self._on_diarization_started)
            transcription_bridge.diarization_completed.connect(self._on_diarization_completed)
            transcription_bridge.diarization_failed.connect(self._on_diarization_failed)

        if summary_bridge is not None:
            summary_bridge.started.connect(self._on_summary_started)
            summary_bridge.completed.connect(self._on_summary_completed)
            summary_bridge.failed.connect(self._on_summary_failed)

        self._refresh_recent_sessions()
        self._apply_state(coordinator.state)

    def announce_recovered_sessions(self) -> None:
        recovered = self._coordinator.recovered_sessions
        if not recovered:
            return
        title = (
            "Interrupted Recording Found" if len(recovered) == 1 else "Interrupted Recordings Found"
        )
        self._tray.show_notification(
            title,
            f"{len(recovered)} recording(s) were interrupted and have been marked for "
            "review in your recordings folder.",
        )

    # -- tray-triggered actions ----------------------------------------------------------

    def _on_start_requested(self) -> None:
        if self._coordinator.state in (RecordingState.SAVED, RecordingState.ERROR):
            self._coordinator.reset()
            self._apply_state(self._coordinator.state)
        if self._coordinator.state is not RecordingState.IDLE:
            return
        self._bridge.check_permissions_async()

    def _on_permissions_checked(self, status: object, error: object) -> None:
        if error is not None:
            self._tray.show_notification("Permission Check Failed", str(error))
            return
        assert isinstance(status, PermissionStatus)
        if not status.all_granted:
            # A permission the user was never asked about has no entry in System
            # Settings' privacy pane to toggle — actively request it (the only path that
            # triggers the real OS consent prompt) before falling back to "open Settings"
            # guidance, which only helps once a prior request has been explicitly denied.
            if (
                status.screen is PermissionState.NOT_DETERMINED
                or status.microphone is PermissionState.NOT_DETERMINED
            ):
                self._bridge.request_permissions_async()
                return
            self._permission_guide_fn(status)
            return
        self._proceed_after_permissions_granted()

    def _on_permissions_requested(self, result: object, error: object) -> None:
        if error is not None:
            self._tray.show_notification("Permission Check Failed", str(error))
            return
        assert isinstance(result, PermissionStatus)
        if not result.all_granted:
            self._permission_guide_fn(result)
            return
        self._proceed_after_permissions_granted()

    def _proceed_after_permissions_granted(self) -> None:
        if not self._consent_fn():
            return
        if self._coordinator.state is not RecordingState.IDLE:
            return  # state moved on while the dialog was open; do not start twice
        self._bridge.start_recording_async(capture_microphone=self._settings.capture_microphone)

    def _on_stop_requested(self) -> None:
        if self._coordinator.state not in (RecordingState.PREPARING, RecordingState.RECORDING):
            return
        self._bridge.stop_recording_async()

    def _on_open_recordings_requested(self) -> None:
        open_path(self._store.paths.recordings_dir)

    def _on_recent_session_requested(self, session_id: str) -> None:
        metadata = self._safe_read_session(session_id)
        viewable = (
            TranscriptionStatus.DONE,
            TranscriptionStatus.PARTIAL,
            TranscriptionStatus.FAILED,
        )
        if metadata is not None and metadata.post_processing.transcript_status in viewable:
            self._show_transcript_dialog(metadata)
            return
        open_path(self._store.session_dir(session_id))

    def _safe_read_session(self, session_id: str) -> SessionMetadata | None:
        try:
            return self._store.read(session_id)
        except (OSError, ValueError):
            return None

    def _show_transcript_dialog(self, metadata: SessionMetadata) -> None:
        transcript = None
        has_transcript_file = metadata.post_processing.transcript_status in (
            TranscriptionStatus.DONE,
            TranscriptionStatus.PARTIAL,
        )
        if has_transcript_file:
            try:
                transcript = self._transcript_store.read(
                    self._store.session_dir(metadata.session_id)
                )
            except (OSError, ValueError):
                transcript = None

        def retry() -> None:
            if self._transcription_manager is not None:
                self._transcription_manager.transcribe_async(metadata)

        def retry_diarization() -> None:
            if self._transcription_manager is not None:
                self._transcription_manager.diarize_async(metadata)

        def rename_speaker(speaker_id: str, new_name: str) -> None:
            self._rename_speaker(metadata, speaker_id, new_name)

        def view_summary() -> None:
            self._show_summary_dialog(metadata)

        self._transcript_dialog_fn(
            metadata,
            transcript,
            on_retry=retry,
            on_retry_diarization=retry_diarization,
            on_rename_speaker=rename_speaker,
            on_view_summary=view_summary,
        )

    def _show_summary_dialog(self, metadata: SessionMetadata) -> None:
        session_dir = self._store.session_dir(metadata.session_id)
        try:
            summary_set = self._summary_store.read(session_dir)
        except (OSError, ValueError):
            summary_set = None
        latest = summary_set.latest if summary_set is not None else None
        version_count = len(summary_set.versions) if summary_set is not None else 0

        def regenerate() -> None:
            if self._summary_manager is not None:
                self._summary_manager.generate_async(metadata)

        self._summary_dialog_fn(
            metadata, latest, version_count=version_count, on_regenerate=regenerate
        )

    def _rename_speaker(self, metadata: SessionMetadata, speaker_id: str, new_name: str) -> None:
        session_dir = self._store.session_dir(metadata.session_id)
        try:
            transcript = self._transcript_store.read(session_dir)
        except (OSError, ValueError):
            return
        updated_speakers = [
            speaker.model_copy(update={"display_name": new_name})
            if speaker.speaker_id == speaker_id
            else speaker
            for speaker in transcript.speakers
        ]
        updated = transcript.model_copy(update={"speakers": updated_speakers})
        self._transcript_store.write(session_dir, updated)

    def _on_settings_requested(self, *, initial_tab: str | None = None) -> None:
        if self._settings_dialog_context is None:
            return
        self._settings_dialog_fn(self._settings_dialog_context, initial_tab=initial_tab)

    def _on_quit_requested(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # -- coordinator-triggered updates ---------------------------------------------------

    def _on_state_changed(self, state: object) -> None:
        assert isinstance(state, RecordingState)
        self._apply_state(state)
        if state is RecordingState.SAVED:
            self._refresh_recent_sessions()
            self._maybe_start_transcription()

    def _maybe_start_transcription(self) -> None:
        if self._transcription_manager is None:
            return
        session = self._coordinator.current_session
        if session is None:
            return
        self._transcription_manager.transcribe_async(session)

    # -- transcription-triggered updates --------------------------------------------------

    def _on_transcription_started(self, session_id: str) -> None:
        self._transcription_progress.pop(session_id, None)
        self._refresh_recent_sessions()

    def _on_transcription_progress(self, progress: object) -> None:
        assert isinstance(progress, TranscriptionProgress)
        if progress.total_s and progress.total_s > 0:
            pct = min(100.0, 100.0 * progress.processed_s / progress.total_s)
            self._transcription_progress[progress.session_id] = pct
        self._refresh_recent_sessions()

    def _on_transcription_completed(self, session_id: str, transcript: object) -> None:
        self._transcription_progress.pop(session_id, None)
        self._refresh_recent_sessions()
        # Desired sequence: Transcription -> Diarization -> Summarization. Only start
        # summary generation immediately when no diarization job was actually started
        # for this session (disabled, no transcription manager/settings, or no eligible
        # system audio) — otherwise `_on_diarization_completed`/`_on_diarization_failed`
        # starts it once diarization is done, so a summary always consumes the final,
        # fully-processed transcript when diarization is enabled, and still runs (using
        # whatever transcript exists) if diarization fails.
        if not self._maybe_start_diarization(session_id):
            self._maybe_start_summary(session_id)

    def _maybe_start_diarization(self, session_id: str) -> bool:
        """Returns ``True`` only if a diarization background job was actually started
        for this session — the caller must then wait for
        ``on_diarization_completed``/``on_diarization_failed`` before starting summary
        generation. ``False`` means no job is running (diarization disabled, or some
        other reason `diarize_async` declined), so the caller should proceed without
        waiting."""
        if self._transcription_manager is None or self._settings_service is None:
            return False
        if not self._settings_service.current.diarization_enabled:
            return False
        metadata = self._safe_read_session(session_id)
        if metadata is None:
            return False
        return self._transcription_manager.diarize_async(metadata)

    def _on_diarization_started(self, session_id: str) -> None:
        self._refresh_recent_sessions()

    def _on_diarization_completed(self, session_id: str, transcript: object) -> None:
        self._refresh_recent_sessions()
        self._maybe_start_summary(session_id)

    def _on_diarization_failed(self, session_id: str, code: str, message: str) -> None:
        log.error("diarization failed", extra={"session_id": session_id, "code": code})
        self._refresh_recent_sessions()
        # The transcript itself is untouched by a diarization failure (see
        # TranscriptionManager._run_diarization) — summarization must still proceed with
        # whatever transcript data is available, never left waiting forever on a
        # diarization job that will never call back again.
        self._maybe_start_summary(session_id)

    # -- summary-triggered updates ---------------------------------------------------------

    def _maybe_start_summary(self, session_id: str) -> None:
        """Independent of diarization: a summary never waits for speaker identification,
        only for a transcript to exist — see ``summarization/manager.py``."""
        if self._summary_manager is None or self._settings_service is None:
            return
        if not self._settings_service.current.summary_enabled:
            return
        metadata = self._safe_read_session(session_id)
        if metadata is None:
            return
        self._summary_manager.generate_async(metadata)

    def _on_summary_started(self, session_id: str) -> None:
        self._refresh_recent_sessions()

    def _on_summary_completed(self, session_id: str, summary: object) -> None:
        self._refresh_recent_sessions()

    def _on_summary_failed(self, session_id: str, code: str, message: str) -> None:
        log.error("summary generation failed", extra={"session_id": session_id, "code": code})
        self._last_notification_settings_tab = TAB_SUMMARY if code == MISSING_API_KEY_CODE else None
        title = "Summary Failed"
        if self._last_notification_settings_tab is not None:
            title += " — click to open Settings"
        self._tray.show_notification(title, message)
        self._refresh_recent_sessions()

    def _on_transcription_failed(self, session_id: str, code: str, message: str) -> None:
        self._transcription_progress.pop(session_id, None)
        log.error("transcription failed", extra={"session_id": session_id, "code": code})
        self._last_notification_settings_tab = (
            TAB_TRANSCRIPTION if code == MISSING_API_KEY_CODE else None
        )
        title = "Transcription Failed"
        if self._last_notification_settings_tab is not None:
            title += " — click to open Settings"
        self._tray.show_notification(title, message)
        self._refresh_recent_sessions()

    def _on_notification_clicked(self) -> None:
        if self._last_notification_settings_tab is not None:
            self._on_settings_requested(initial_tab=self._last_notification_settings_tab)

    def _apply_state(self, state: RecordingState) -> None:
        detail = None
        if state is RecordingState.ERROR:
            last_error = self._coordinator.last_error
            detail = last_error[1] if last_error else None
        self._tray.set_state(state, detail=detail)
        if state is RecordingState.RECORDING:
            self._duration_timer.start()
        else:
            self._duration_timer.stop()

    def _update_duration(self) -> None:
        session = self._coordinator.current_session
        if session is None or session.started_at is None:
            return
        elapsed = (utc_now() - session.started_at).total_seconds()
        self._tray.set_duration(elapsed)

    def _on_operation_failed(self, code: str, message: str) -> None:
        log.error("recording operation failed", extra={"code": code})
        self._tray.show_notification("Recording Error", message)

    def _on_warning_raised(self, warning: object) -> None:
        assert isinstance(warning, WarningInfo)
        self._tray.show_notification("Warning", warning.message)

    def _refresh_recent_sessions(self) -> None:
        self._tray.set_recent_sessions(
            self._store.list_sessions()[:10],
            transcription_progress=self._transcription_progress,
        )
