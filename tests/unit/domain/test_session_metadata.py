from __future__ import annotations

import json

from local_meeting_recorder.domain import AudioTrackInfo, SessionMetadata, SessionStatus


def test_defaults_are_in_progress() -> None:
    meta = SessionMetadata(session_id="lmr_20261015T000000_0000")
    assert meta.status is SessionStatus.IN_PROGRESS
    assert not meta.status.is_terminal
    assert meta.recording_file == "recording.mov"
    assert meta.started_at is None


def test_lifecycle_markers() -> None:
    meta = SessionMetadata(session_id="lmr_20261015T000000_0000")
    meta.mark_started(helper_version="1.0.0")
    assert meta.started_at is not None
    assert meta.last_heartbeat_at == meta.started_at
    first = meta.last_heartbeat_at
    meta.heartbeat()
    assert meta.last_heartbeat_at is not None and meta.last_heartbeat_at >= first
    meta.mark_saved(
        duration_s=12.5,
        video_frames=375,
        audio_tracks=[
            AudioTrackInfo(index=1, role="system"),
            AudioTrackInfo(index=2, role="microphone"),
        ],
    )
    assert meta.status is SessionStatus.SAVED
    assert meta.status.is_terminal
    assert meta.finalized_at is not None
    assert [t.role for t in meta.audio_tracks] == ["system", "microphone"]


def test_failure_keeps_error_details_and_round_trips_json() -> None:
    meta = SessionMetadata(session_id="lmr_20261015T000000_0000")
    meta.mark_failed(code="helper_crashed", message="exit 134")
    data = json.loads(meta.model_dump_json())
    restored = SessionMetadata.model_validate(data)
    assert restored.status is SessionStatus.FAILED
    assert restored.error_code == "helper_crashed"
    assert restored.error_message == "exit 134"
    assert restored.schema_version == 1
