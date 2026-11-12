"""TrayController: pure presentation, no coordinator/backend involved."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.domain.session_metadata import (
    DiarizationStatus,
    SessionMetadata,
    TranscriptionStatus,
)
from local_meeting_recorder.ui.tray import TrayController


def test_start_enabled_only_when_idle_saved_or_error(qapp: QApplication) -> None:
    tray = TrayController()
    for state in (RecordingState.IDLE, RecordingState.SAVED, RecordingState.ERROR):
        tray.set_state(state)
        assert tray._start_action.isEnabled(), state
    for state in (RecordingState.PREPARING, RecordingState.RECORDING, RecordingState.FINALIZING):
        tray.set_state(state)
        assert not tray._start_action.isEnabled(), state


def test_stop_enabled_only_while_preparing_or_recording(qapp: QApplication) -> None:
    tray = TrayController()
    for state in (RecordingState.PREPARING, RecordingState.RECORDING):
        tray.set_state(state)
        assert tray._stop_action.isEnabled(), state
    for state in (
        RecordingState.IDLE,
        RecordingState.FINALIZING,
        RecordingState.SAVED,
        RecordingState.ERROR,
    ):
        tray.set_state(state)
        assert not tray._stop_action.isEnabled(), state


def test_status_text_includes_error_detail(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_state(RecordingState.ERROR, detail="helper crashed")
    assert tray._status_action.text() == "Error: helper crashed"


def test_status_text_has_no_detail_by_default(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_state(RecordingState.IDLE)
    assert tray._status_action.text() == "Idle"


def test_duration_only_updates_text_while_recording(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_state(RecordingState.RECORDING)
    tray.set_duration(75)
    assert tray._status_action.text() == "Recording — 1:15"

    tray.set_state(RecordingState.SAVED)
    before = tray._status_action.text()
    tray.set_duration(999)  # stale timer tick arriving after the state already moved on
    assert tray._status_action.text() == before


def test_recent_sessions_populate_submenu(qapp: QApplication) -> None:
    tray = TrayController()
    sessions = [
        SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup"),
        SessionMetadata(session_id="lmr_20261104T000001_bbbb"),
    ]
    tray.set_recent_sessions(sessions)
    assert tray._recent_menu.isEnabled()
    labels = [action.text() for action in tray._recent_menu.actions()]
    assert labels == ["Standup", "lmr_20261104T000001_bbbb"]


def test_recent_sessions_disabled_submenu_when_empty(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_recent_sessions([])
    assert not tray._recent_menu.isEnabled()


def test_clicking_recent_session_emits_its_id(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_recent_sessions([SessionMetadata(session_id="lmr_20261104T000002_cccc")])
    emitted: list[str] = []
    tray.recent_session_requested.connect(emitted.append)
    tray._recent_menu.actions()[0].trigger()
    assert emitted == ["lmr_20261104T000002_cccc"]


def test_recent_sessions_show_no_suffix_when_not_started(qapp: QApplication) -> None:
    tray = TrayController()
    tray.set_recent_sessions(
        [SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")]
    )
    assert tray._recent_menu.actions()[0].text() == "Standup"


def test_recent_sessions_show_transcribing_suffix(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.RUNNING
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == "Standup — Transcribing…"


def test_recent_sessions_show_progress_percentage_while_transcribing(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.RUNNING
    tray.set_recent_sessions([metadata], transcription_progress={metadata.session_id: 42.3})
    assert tray._recent_menu.actions()[0].text() == "Standup — Transcribing… 42%"


def test_recent_sessions_show_transcribed_with_language(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.DONE
    metadata.post_processing.transcript_language = "en"
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == "Standup — Transcribed (en)"


def test_recent_sessions_show_failed_suffix(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.FAILED
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == "Standup — Transcription failed"


def test_recent_sessions_show_partial_suffix(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.PARTIAL
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == "Standup — Transcribed (partial)"


def test_recent_sessions_show_speakers_identified_suffix(qapp: QApplication) -> None:
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.DONE
    metadata.post_processing.transcript_language = "en"
    metadata.post_processing.diarization_status = DiarizationStatus.DONE
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == (
        "Standup — Transcribed (en), speakers identified"
    )


def test_recent_sessions_show_partially_identified_suffix_when_a_speaker_is_unknown(
    qapp: QApplication,
) -> None:
    """A diarization run that leaves at least one segment ``UNKNOWN_SPEAKER_ID`` must
    not be reported the same way as a fully-resolved one."""
    tray = TrayController()
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa", title="Standup")
    metadata.post_processing.transcript_status = TranscriptionStatus.DONE
    metadata.post_processing.transcript_language = "en"
    metadata.post_processing.diarization_status = DiarizationStatus.DONE
    metadata.post_processing.diarization_has_unknown_speaker = True
    tray.set_recent_sessions([metadata])
    assert tray._recent_menu.actions()[0].text() == (
        "Standup — Transcribed (en), speakers partially identified"
    )


def test_start_and_stop_actions_emit_signals(qapp: QApplication) -> None:
    tray = TrayController()
    starts = []
    stops = []
    tray.start_requested.connect(lambda: starts.append(1))
    tray.stop_requested.connect(lambda: stops.append(1))

    tray._start_action.trigger()  # enabled while Idle
    assert starts == [1]

    tray.set_state(RecordingState.RECORDING)  # Stop is only enabled while active
    tray._stop_action.trigger()
    assert stops == [1]


def test_settings_menu_item_emits_settings_requested(qapp: QApplication) -> None:
    tray = TrayController()
    requests: list[int] = []
    tray.settings_requested.connect(lambda: requests.append(1))

    settings_action = next(a for a in tray._menu.actions() if a.text() == "Settings…")
    settings_action.trigger()
    assert requests == [1]
