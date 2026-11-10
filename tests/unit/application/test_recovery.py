"""recover_incomplete_sessions: never touches the recording file, only session.json."""

from __future__ import annotations

from pathlib import Path

from local_meeting_recorder.application.recovery import recover_incomplete_sessions
from local_meeting_recorder.domain.session_metadata import SessionMetadata, SessionStatus
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore


def _store(data_dir: Path) -> SessionStore:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    return SessionStore(paths)


def test_recovers_in_progress_session_with_a_recording_file(data_dir: Path) -> None:
    store = _store(data_dir)
    metadata = SessionMetadata(session_id="lmr_20261104T000000_aaaa")
    store.create(metadata)
    recording_path = store.recording_path(metadata)
    recording_path.write_bytes(b"not a real mov but non-empty")

    recovered = recover_incomplete_sessions(store)

    assert len(recovered) == 1
    assert recovered[0].status is SessionStatus.RECOVERED
    assert recovered[0].error_code == "interrupted"
    # The recording file itself must be untouched.
    assert recording_path.read_bytes() == b"not a real mov but non-empty"
    reread = store.read(metadata.session_id)
    assert reread.status is SessionStatus.RECOVERED


def test_marks_unrecoverable_when_no_recording_file_exists(data_dir: Path) -> None:
    store = _store(data_dir)
    metadata = SessionMetadata(session_id="lmr_20261104T000001_bbbb")
    store.create(metadata)
    # No recording.mov was ever written (crash happened before the helper produced media).

    recovered = recover_incomplete_sessions(store)

    assert len(recovered) == 1
    assert recovered[0].status is SessionStatus.UNRECOVERABLE
    assert recovered[0].error_code == "no_recording_file"


def test_marks_unrecoverable_when_recording_file_is_empty(data_dir: Path) -> None:
    store = _store(data_dir)
    metadata = SessionMetadata(session_id="lmr_20261104T000002_cccc")
    store.create(metadata)
    store.recording_path(metadata).touch()  # zero bytes

    recovered = recover_incomplete_sessions(store)

    assert recovered[0].status is SessionStatus.UNRECOVERABLE


def test_leaves_already_terminal_sessions_alone(data_dir: Path) -> None:
    store = _store(data_dir)
    saved = SessionMetadata(session_id="lmr_20261104T000003_dddd")
    saved.mark_saved(duration_s=5.0, video_frames=10)
    store.create(saved)

    recovered = recover_incomplete_sessions(store)

    assert recovered == []
    reread = store.read(saved.session_id)
    assert reread.status is SessionStatus.SAVED


def test_no_incomplete_sessions_returns_empty_list(data_dir: Path) -> None:
    store = _store(data_dir)
    assert recover_incomplete_sessions(store) == []


def test_recovers_multiple_incomplete_sessions(data_dir: Path) -> None:
    store = _store(data_dir)
    for session_id, has_file in (
        ("lmr_20261104T000010_aaaa", True),
        ("lmr_20261104T000011_bbbb", False),
    ):
        metadata = SessionMetadata(session_id=session_id)
        store.create(metadata)
        if has_file:
            store.recording_path(metadata).write_bytes(b"x")

    recovered = recover_incomplete_sessions(store)

    assert len(recovered) == 2
    statuses = {m.status for m in recovered}
    assert statuses == {SessionStatus.RECOVERED, SessionStatus.UNRECOVERABLE}
