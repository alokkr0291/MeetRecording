"""AppController: the check-permissions -> consent -> start flow, and UI state wiring.

Real TrayController and CoordinatorBridge (so the actual Qt plumbing is exercised), a
FakeCaptureBackend-backed coordinator, and injected consent/permission-guide callables so
no real modal dialog needs to appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from local_meeting_recorder.application.recording_coordinator import RecordingCoordinator
from local_meeting_recorder.capture.fake import FakeCaptureBackend
from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    PermissionState,
    PermissionStatus,
)
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import CaptureError
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import (
    SessionMetadata,
    SessionStatus,
    TranscriptionStatus,
)
from local_meeting_recorder.settings.service import SettingsService
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.transcription.models import AudioSource, Speaker, Transcript
from local_meeting_recorder.transcription.providers.factory import MISSING_API_KEY_CODE
from local_meeting_recorder.transcription.store import TranscriptStore
from local_meeting_recorder.ui.app_controller import AppController
from local_meeting_recorder.ui.coordinator_bridge import CoordinatorBridge
from local_meeting_recorder.ui.tray import TrayController
from tests.unit.ui.conftest import pump_until


class _Spy:
    def __init__(self, return_value: object = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.return_value = return_value

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.return_value


class _FakeTranscriptionManager:
    def __init__(self, *, diarize_result: bool = True) -> None:
        self.calls: list[SessionMetadata] = []
        self.diarize_calls: list[SessionMetadata] = []
        self._diarize_result = diarize_result

    def transcribe_async(self, metadata: SessionMetadata, *, language: str | None = None) -> bool:
        self.calls.append(metadata)
        return True

    def diarize_async(self, metadata: SessionMetadata) -> bool:
        self.diarize_calls.append(metadata)
        return self._diarize_result


class _FakeSummaryManager:
    def __init__(self) -> None:
        self.calls: list[SessionMetadata] = []

    def generate_async(self, metadata: SessionMetadata) -> bool:
        self.calls.append(metadata)
        return True


def _build(
    data_dir: Path,
    *,
    fake: FakeCaptureBackend | None = None,
    consent_fn: object = None,
    permission_guide_fn: object = None,
    recover_on_init: bool = False,
    transcription_manager: object = None,
    transcript_dialog_fn: object = None,
    settings_dialog_context: object = None,
    settings_dialog_fn: object = None,
    settings_service: SettingsService | None = None,
    summary_manager: object = None,
    summary_dialog_fn: object = None,
) -> tuple[AppController, TrayController, RecordingCoordinator, FakeCaptureBackend, SessionStore]:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    store = SessionStore(paths)
    backend = fake if fake is not None else FakeCaptureBackend()
    bridge = CoordinatorBridge()

    def factory(callbacks: CaptureCallbacks) -> FakeCaptureBackend:
        backend.callbacks = callbacks
        return backend

    settings = Settings(_env_file=None, data_dir=data_dir)
    coordinator = RecordingCoordinator(
        backend_factory=factory,
        session_store=store,
        settings=settings,
        callbacks=bridge.callbacks,
        recover_on_init=recover_on_init,
    )
    bridge.attach(coordinator)
    tray = TrayController()
    kwargs: dict[str, object] = {}
    if transcription_manager is not None:
        kwargs["transcription_manager"] = transcription_manager
    if transcript_dialog_fn is not None:
        kwargs["transcript_dialog_fn"] = transcript_dialog_fn
    if settings_dialog_context is not None:
        kwargs["settings_dialog_context"] = settings_dialog_context
    if settings_dialog_fn is not None:
        kwargs["settings_dialog_fn"] = settings_dialog_fn
    if settings_service is not None:
        kwargs["settings_service"] = settings_service
    if summary_manager is not None:
        kwargs["summary_manager"] = summary_manager
    if summary_dialog_fn is not None:
        kwargs["summary_dialog_fn"] = summary_dialog_fn
    controller = AppController(
        coordinator=coordinator,
        bridge=bridge,
        tray=tray,
        session_store=store,
        settings=settings,
        consent_fn=consent_fn or _Spy(return_value=True),
        permission_guide_fn=permission_guide_fn,
        **kwargs,  # type: ignore[arg-type]
    )
    return controller, tray, coordinator, backend, store


def test_permission_denied_shows_guide_and_never_starts(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.DENIED, microphone=PermissionState.GRANTED
        )
    )
    guide = _Spy()
    _controller, tray, coordinator, _fake, _store = _build(
        data_dir, fake=fake, permission_guide_fn=guide
    )

    tray.start_requested.emit()
    assert pump_until(lambda: len(guide.calls) == 1)
    assert coordinator.state is RecordingState.IDLE
    (status,) = guide.calls[0]
    assert isinstance(status, PermissionStatus)
    assert not status.screen.is_usable


def test_not_determined_permission_actively_requests_before_guide(
    qapp: QApplication, data_dir: Path
) -> None:
    """A never-asked permission has no entry in System Settings to toggle — the app must
    call request_permissions() (the only path that triggers the real OS prompt) rather
    than jumping straight to "open Settings" guidance, which would have nothing to show."""
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.GRANTED, microphone=PermissionState.NOT_DETERMINED
        ),
        permissions_after_request=PermissionStatus(
            screen=PermissionState.GRANTED, microphone=PermissionState.GRANTED
        ),
    )
    guide = _Spy()
    consent = _Spy(return_value=True)
    _controller, tray, coordinator, fake, _store = _build(
        data_dir, fake=fake, permission_guide_fn=guide, consent_fn=consent
    )

    tray.start_requested.emit()
    assert pump_until(lambda: fake.start_calls != [])
    assert guide.calls == []
    assert fake.request_permissions_calls == 1
    assert coordinator.state is not RecordingState.IDLE


def test_not_determined_permission_still_denied_after_request_shows_guide(
    qapp: QApplication, data_dir: Path
) -> None:
    fake = FakeCaptureBackend(
        permissions=PermissionStatus(
            screen=PermissionState.GRANTED, microphone=PermissionState.NOT_DETERMINED
        ),
        permissions_after_request=PermissionStatus(
            screen=PermissionState.GRANTED, microphone=PermissionState.DENIED
        ),
    )
    guide = _Spy()
    _controller, tray, coordinator, fake, _store = _build(
        data_dir, fake=fake, permission_guide_fn=guide
    )

    tray.start_requested.emit()
    assert pump_until(lambda: len(guide.calls) == 1)
    assert fake.request_permissions_calls == 1
    assert fake.start_calls == []
    assert coordinator.state is RecordingState.IDLE


def test_consent_declined_does_not_start(qapp: QApplication, data_dir: Path) -> None:
    consent = _Spy(return_value=False)
    _controller, tray, coordinator, fake, _store = _build(data_dir, consent_fn=consent)

    tray.start_requested.emit()
    assert pump_until(lambda: len(consent.calls) == 1)
    assert coordinator.state is RecordingState.IDLE
    assert fake.start_calls == []


def test_consent_accepted_starts_recording(qapp: QApplication, data_dir: Path) -> None:
    consent = _Spy(return_value=True)
    _controller, tray, coordinator, fake, _store = _build(data_dir, consent_fn=consent)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    assert len(fake.start_calls) == 1


def test_stop_requested_while_idle_is_ignored(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.stop_requested.emit()
    assert coordinator.state is RecordingState.IDLE
    assert fake.stop_calls == 0


def test_full_cycle_updates_tray_status_text(qapp: QApplication, data_dir: Path) -> None:
    # Polls the tray's own text, not coordinator.state: the coordinator can reach a new
    # state on its background thread slightly before the queued signal that updates the
    # tray has actually been delivered, so asserting text right after a state-only poll
    # is a race. Waiting for the text itself is what the test actually cares about.
    _controller, tray, coordinator, _fake, _store = _build(data_dir)

    tray.start_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Recording")
    assert coordinator.state is RecordingState.RECORDING

    tray.stop_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Saved")
    assert coordinator.state is RecordingState.SAVED


def test_start_after_saved_resets_and_starts_again(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    assert len(fake.start_calls) == 2


def test_error_state_shows_detail_in_tray(qapp: QApplication, data_dir: Path) -> None:
    fake = FakeCaptureBackend(fail_start_with=CaptureError("helper would not launch"))
    _controller, tray, coordinator, _fake, _store = _build(data_dir, fake=fake)

    tray.start_requested.emit()
    assert pump_until(lambda: tray._status_action.text() == "Error: helper would not launch")
    assert coordinator.state is RecordingState.ERROR


def test_backend_crash_during_recording_updates_tray_to_error(
    qapp: QApplication, data_dir: Path
) -> None:
    _controller, tray, coordinator, fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)

    fake.simulate_crash(message="helper died")
    assert pump_until(lambda: tray._status_action.text() == "Error: helper died")
    assert coordinator.state is RecordingState.ERROR


def test_duration_reflects_elapsed_time_since_start(qapp: QApplication, data_dir: Path) -> None:
    controller, tray, coordinator, _fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)

    controller._update_duration()
    assert tray._status_action.text().startswith("Recording — 0:0")


def test_announce_recovered_sessions_shows_notification(qapp: QApplication, data_dir: Path) -> None:
    _controller1, _tray1, coord1, _fake1, store = _build(data_dir, recover_on_init=False)
    orphaned_session = coord1.start_recording()
    assert orphaned_session is not None
    with store.recording_path(orphaned_session).open("ab") as fh:
        fh.write(b"some bytes")

    controller, tray, coordinator, _fake2, _store2 = _build(data_dir, recover_on_init=True)
    notifications: list[tuple[str, str]] = []
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]

    assert len(coordinator.recovered_sessions) == 1
    controller.announce_recovered_sessions()
    assert len(notifications) == 1
    assert "Interrupted" in notifications[0][0]


def test_announce_recovered_sessions_silent_when_nothing_was_recovered(
    qapp: QApplication, data_dir: Path
) -> None:
    controller, tray, _coordinator, _fake, _store = _build(data_dir, recover_on_init=True)
    notifications: list[tuple[str, str]] = []
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]
    controller.announce_recovered_sessions()
    assert notifications == []


def test_open_recordings_requested_opens_the_recordings_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", calls.append)
    _controller, tray, _coordinator, _fake, store = _build(data_dir)

    tray.open_recordings_requested.emit()
    assert calls == [store.paths.recordings_dir]


def test_recent_session_requested_opens_that_sessions_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", calls.append)
    _controller, tray, _coordinator, _fake, store = _build(data_dir)

    tray.recent_session_requested.emit("lmr_20261104T000000_aaaa")
    assert calls == [store.session_dir("lmr_20261104T000000_aaaa")]


def test_saved_recording_appears_in_recent_sessions(qapp: QApplication, data_dir: Path) -> None:
    _controller, tray, coordinator, _fake, store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].status is SessionStatus.SAVED


# -- transcription integration --------------------------------------------------------


def test_saved_recording_triggers_transcription(qapp: QApplication, data_dir: Path) -> None:
    manager = _FakeTranscriptionManager()
    _controller, tray, coordinator, _fake, _store = _build(data_dir, transcription_manager=manager)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)
    assert pump_until(lambda: len(manager.calls) == 1)
    assert manager.calls[0].session_id == coordinator.current_session.session_id  # type: ignore[union-attr]


def test_without_a_transcription_manager_saved_state_does_not_crash(
    qapp: QApplication, data_dir: Path
) -> None:
    _controller, tray, coordinator, _fake, _store = _build(data_dir)
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)  # no AttributeError


def test_recent_session_without_a_transcript_opens_the_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", calls.append)
    _controller, tray, coordinator, _fake, store = _build(data_dir)

    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    session_id = coordinator.current_session.session_id  # type: ignore[union-attr]
    tray.recent_session_requested.emit(session_id)
    assert calls == [store.session_dir(session_id)]


def test_recent_session_with_a_finished_transcript_opens_the_dialog_instead_of_the_folder(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_calls: list[Path] = []
    monkeypatch.setattr("local_meeting_recorder.ui.app_controller.open_path", open_calls.append)
    dialog_calls: list[tuple[SessionMetadata, Transcript | None]] = []

    def fake_dialog(metadata: SessionMetadata, transcript: Transcript | None, **_: object) -> None:
        dialog_calls.append((metadata, transcript))

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    metadata = coordinator.current_session
    assert metadata is not None
    transcript = Transcript(session_id=metadata.session_id, language="en", model_name="small")
    TranscriptStore().write(store.session_dir(metadata.session_id), transcript)
    metadata.mark_transcription_done(
        transcript_file="transcript.json", language="en", model="small"
    )
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert open_calls == []
    assert len(dialog_calls) == 1
    seen_metadata, seen_transcript = dialog_calls[0]
    assert seen_metadata.session_id == metadata.session_id
    assert seen_transcript == transcript


def test_recent_session_with_failed_transcript_opens_dialog_with_no_transcript_object(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog_calls: list[tuple[SessionMetadata, Transcript | None]] = []

    def fake_dialog(metadata: SessionMetadata, transcript: Transcript | None, **_: object) -> None:
        dialog_calls.append((metadata, transcript))

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    metadata = coordinator.current_session
    assert metadata is not None
    metadata.mark_transcription_failed(message="ffmpeg exploded")
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert len(dialog_calls) == 1
    seen_metadata, seen_transcript = dialog_calls[0]
    assert seen_metadata.post_processing.transcript_status is TranscriptionStatus.FAILED
    assert seen_transcript is None


def test_recent_session_with_partial_transcript_opens_dialog_with_the_transcript(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog_calls: list[tuple[SessionMetadata, Transcript | None]] = []

    def fake_dialog(metadata: SessionMetadata, transcript: Transcript | None, **_: object) -> None:
        dialog_calls.append((metadata, transcript))

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    metadata = coordinator.current_session
    assert metadata is not None
    transcript = Transcript(session_id=metadata.session_id, language="en", model_name="small")
    TranscriptStore().write(store.session_dir(metadata.session_id), transcript)
    metadata.mark_transcription_partial(
        transcript_file="transcript.json",
        language="en",
        model="small",
        message="microphone: boom",
    )
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert len(dialog_calls) == 1
    seen_metadata, seen_transcript = dialog_calls[0]
    assert seen_metadata.post_processing.transcript_status is TranscriptionStatus.PARTIAL
    assert seen_transcript == transcript


def test_retry_callback_passed_to_dialog_calls_transcription_manager(
    qapp: QApplication, data_dir: Path
) -> None:
    manager = _FakeTranscriptionManager()
    retry_fns: list[object] = []

    def fake_dialog(
        metadata: SessionMetadata, transcript: Transcript | None, *, on_retry: object, **_: object
    ) -> None:
        retry_fns.append(on_retry)

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcription_manager=manager, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)
    manager.calls.clear()  # drop the automatic post-SAVED trigger; we only care about retry

    metadata = coordinator.current_session
    assert metadata is not None
    metadata.mark_transcription_failed(message="boom")
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert len(retry_fns) == 1
    retry_fns[0]()  # type: ignore[operator]
    assert len(manager.calls) == 1
    assert manager.calls[0].session_id == metadata.session_id


def test_settings_requested_opens_the_settings_dialog_with_its_context(
    qapp: QApplication, data_dir: Path
) -> None:
    sentinel_context = object()
    calls: list[tuple[object, str | None]] = []
    _controller, tray, _coordinator, _fake, _store = _build(
        data_dir,
        settings_dialog_context=sentinel_context,
        settings_dialog_fn=lambda ctx, *, initial_tab=None: calls.append((ctx, initial_tab)),
    )

    tray.settings_requested.emit()
    assert calls == [(sentinel_context, None)]


def test_settings_requested_without_a_context_does_nothing(
    qapp: QApplication, data_dir: Path
) -> None:
    _controller, tray, _coordinator, _fake, _store = _build(data_dir)
    tray.settings_requested.emit()  # must not raise


def test_missing_api_key_notification_click_opens_settings_on_the_transcription_tab(
    qapp: QApplication, data_dir: Path
) -> None:
    sentinel_context = object()
    calls: list[tuple[object, str | None]] = []
    controller, tray, _coordinator, _fake, _store = _build(
        data_dir,
        settings_dialog_context=sentinel_context,
        settings_dialog_fn=lambda ctx, *, initial_tab=None: calls.append((ctx, initial_tab)),
    )

    controller._on_transcription_failed("session-1", MISSING_API_KEY_CODE, "no key configured")
    tray._tray.messageClicked.emit()
    assert calls == [(sentinel_context, "Transcription")]


def test_other_failure_notification_click_does_not_open_settings(
    qapp: QApplication, data_dir: Path
) -> None:
    sentinel_context = object()
    calls: list[tuple[object, str | None]] = []
    controller, tray, _coordinator, _fake, _store = _build(
        data_dir,
        settings_dialog_context=sentinel_context,
        settings_dialog_fn=lambda ctx, *, initial_tab=None: calls.append((ctx, initial_tab)),
    )

    controller._on_transcription_failed("session-1", "audio_extraction_failed", "ffmpeg exploded")
    tray._tray.messageClicked.emit()
    assert calls == []


def test_missing_api_key_summary_failure_shows_a_clickable_tray_notification(
    qapp: QApplication, data_dir: Path
) -> None:
    sentinel_context = object()
    calls: list[tuple[object, str | None]] = []
    notifications: list[tuple[str, str]] = []
    controller, tray, _coordinator, _fake, _store = _build(
        data_dir,
        settings_dialog_context=sentinel_context,
        settings_dialog_fn=lambda ctx, *, initial_tab=None: calls.append((ctx, initial_tab)),
    )
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]

    controller._on_summary_failed("session-1", MISSING_API_KEY_CODE, "no key configured")
    assert len(notifications) == 1
    assert "click to open Settings" in notifications[0][0]

    tray._tray.messageClicked.emit()
    assert calls == [(sentinel_context, "AI Summary")]


def test_other_summary_failure_does_not_offer_to_open_settings(
    qapp: QApplication, data_dir: Path
) -> None:
    sentinel_context = object()
    calls: list[tuple[object, str | None]] = []
    notifications: list[tuple[str, str]] = []
    controller, tray, _coordinator, _fake, _store = _build(
        data_dir,
        settings_dialog_context=sentinel_context,
        settings_dialog_fn=lambda ctx, *, initial_tab=None: calls.append((ctx, initial_tab)),
    )
    tray.show_notification = lambda title, message: notifications.append((title, message))  # type: ignore[method-assign]

    controller._on_summary_failed("session-1", "summary_malformed_output", "bad JSON")
    assert notifications == [("Summary Failed", "bad JSON")]

    tray._tray.messageClicked.emit()
    assert calls == []


# -- diarization integration ------------------------------------------------------------


def test_transcription_completion_starts_diarization_when_enabled(
    qapp: QApplication, data_dir: Path
) -> None:
    manager = _FakeTranscriptionManager()
    settings_service = SettingsService(data_dir / "settings.json")
    settings_service.update(diarization_enabled=True)
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir, transcription_manager=manager, settings_service=settings_service
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert len(manager.diarize_calls) == 1
    assert manager.diarize_calls[0].session_id == metadata.session_id


def test_transcription_completion_does_not_start_diarization_when_disabled(
    qapp: QApplication, data_dir: Path
) -> None:
    manager = _FakeTranscriptionManager()
    settings_service = SettingsService(data_dir / "settings.json")
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir, transcription_manager=manager, settings_service=settings_service
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert manager.diarize_calls == []


def test_transcription_completion_without_a_settings_service_does_not_crash(
    qapp: QApplication, data_dir: Path
) -> None:
    manager = _FakeTranscriptionManager()
    controller, _tray, _coordinator, _fake, store = _build(data_dir, transcription_manager=manager)
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())  # no AttributeError
    assert manager.diarize_calls == []


def test_retry_diarization_callback_passed_to_dialog_calls_diarize_async(
    qapp: QApplication, data_dir: Path
) -> None:
    manager = _FakeTranscriptionManager()
    diarize_retry_fns: list[object] = []

    def fake_dialog(
        metadata: SessionMetadata,
        transcript: Transcript | None,
        *,
        on_retry_diarization: object,
        **_: object,
    ) -> None:
        diarize_retry_fns.append(on_retry_diarization)

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcription_manager=manager, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)
    manager.calls.clear()

    metadata = coordinator.current_session
    assert metadata is not None
    transcript = Transcript(session_id=metadata.session_id, language="en", model_name="small")
    TranscriptStore().write(store.session_dir(metadata.session_id), transcript)
    metadata.mark_transcription_done(
        transcript_file="transcript.json", language="en", model="small"
    )
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert len(diarize_retry_fns) == 1
    diarize_retry_fns[0]()  # type: ignore[operator]
    assert len(manager.diarize_calls) == 1
    assert manager.diarize_calls[0].session_id == metadata.session_id


def test_diarization_failure_callback_does_not_crash(qapp: QApplication, data_dir: Path) -> None:
    controller, _tray, _coordinator, _fake, _store = _build(data_dir)
    controller._on_diarization_failed("session-1", "diarization_failed", "no hf token")  # no raise


# -- processing order: transcription -> diarization -> summarization --------------------


def test_summary_waits_for_diarization_to_complete_before_starting(
    qapp: QApplication, data_dir: Path
) -> None:
    """Desired sequence when diarization is enabled: summary generation must not start
    until diarization finishes, so it consumes the diarized transcript."""
    transcription_manager = _FakeTranscriptionManager()
    summary_manager = _FakeSummaryManager()
    settings_service = SettingsService(data_dir / "settings.json")
    settings_service.update(diarization_enabled=True, summary_enabled=True)
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir,
        transcription_manager=transcription_manager,
        summary_manager=summary_manager,
        settings_service=settings_service,
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert len(transcription_manager.diarize_calls) == 1
    assert summary_manager.calls == []  # not yet — diarization hasn't finished

    controller._on_diarization_completed(metadata.session_id, object())
    assert len(summary_manager.calls) == 1
    assert summary_manager.calls[0].session_id == metadata.session_id


def test_summary_still_runs_after_diarization_fails(qapp: QApplication, data_dir: Path) -> None:
    """If diarization fails, summarization must still proceed using the (unaffected)
    transcript rather than waiting forever."""
    transcription_manager = _FakeTranscriptionManager()
    summary_manager = _FakeSummaryManager()
    settings_service = SettingsService(data_dir / "settings.json")
    settings_service.update(diarization_enabled=True, summary_enabled=True)
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir,
        transcription_manager=transcription_manager,
        summary_manager=summary_manager,
        settings_service=settings_service,
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert summary_manager.calls == []

    controller._on_diarization_failed(metadata.session_id, "diarization_failed", "no hf token")
    assert len(summary_manager.calls) == 1


def test_summary_starts_immediately_when_diarization_is_disabled(
    qapp: QApplication, data_dir: Path
) -> None:
    transcription_manager = _FakeTranscriptionManager()
    summary_manager = _FakeSummaryManager()
    settings_service = SettingsService(data_dir / "settings.json")
    settings_service.update(diarization_enabled=False, summary_enabled=True)
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir,
        transcription_manager=transcription_manager,
        summary_manager=summary_manager,
        settings_service=settings_service,
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert transcription_manager.diarize_calls == []
    assert len(summary_manager.calls) == 1  # no diarization job to wait for


def test_summary_starts_immediately_when_diarize_async_declines_to_run(
    qapp: QApplication, data_dir: Path
) -> None:
    """``diarize_async`` returning ``False`` means no job was actually started (e.g. one
    was already running for this session) — nothing will ever call back, so summary
    must not be left waiting on it."""
    transcription_manager = _FakeTranscriptionManager(diarize_result=False)
    summary_manager = _FakeSummaryManager()
    settings_service = SettingsService(data_dir / "settings.json")
    settings_service.update(diarization_enabled=True, summary_enabled=True)
    controller, _tray, _coordinator, _fake, store = _build(
        data_dir,
        transcription_manager=transcription_manager,
        summary_manager=summary_manager,
        settings_service=settings_service,
    )
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)

    controller._on_transcription_completed(metadata.session_id, object())
    assert len(transcription_manager.diarize_calls) == 1
    assert len(summary_manager.calls) == 1


def test_rename_speaker_callback_updates_and_persists_display_name(
    qapp: QApplication, data_dir: Path
) -> None:
    rename_fns: list[object] = []

    def fake_dialog(
        metadata: SessionMetadata,
        transcript: Transcript | None,
        *,
        on_rename_speaker: object,
        **_: object,
    ) -> None:
        rename_fns.append(on_rename_speaker)

    _controller, tray, coordinator, _fake, store = _build(
        data_dir, transcript_dialog_fn=fake_dialog
    )
    tray.start_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.RECORDING)
    tray.stop_requested.emit()
    assert pump_until(lambda: coordinator.state is RecordingState.SAVED)

    metadata = coordinator.current_session
    assert metadata is not None
    speaker = Speaker(
        speaker_id="speaker_1", display_name="Speaker 1", source_type=AudioSource.SYSTEM
    )
    transcript = Transcript(
        session_id=metadata.session_id, language="en", model_name="small", speakers=[speaker]
    )
    TranscriptStore().write(store.session_dir(metadata.session_id), transcript)
    metadata.mark_transcription_done(
        transcript_file="transcript.json", language="en", model="small"
    )
    store.write(metadata)

    tray.recent_session_requested.emit(metadata.session_id)
    assert len(rename_fns) == 1
    rename_fns[0]("speaker_1", "Alex")  # type: ignore[operator]

    updated = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert updated.speakers[0].display_name == "Alex"
