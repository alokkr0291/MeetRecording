from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from local_meeting_recorder.domain import SessionMetadata, SessionStatus
from local_meeting_recorder.storage.paths import (
    HELPER_BINARY_NAME,
    StoragePaths,
    default_data_dir,
    resolve_helper_binary,
)
from local_meeting_recorder.storage.session_store import SessionStore


def test_default_data_dir_is_under_application_support() -> None:
    d = default_data_dir()
    if sys.platform == "darwin":
        assert d == Path.home() / "Library" / "Application Support" / "LocalMeetingRecorder"
    assert d.name == "LocalMeetingRecorder"


def test_paths_layout_and_ensure(data_dir: Path) -> None:
    p = StoragePaths(root=data_dir)
    assert p.recordings_dir == data_dir / "recordings"
    assert p.logs_dir == data_dir / "logs"
    assert p.session_dir("abc") == data_dir / "recordings" / "abc"
    assert not data_dir.exists()
    p.ensure()
    assert p.recordings_dir.is_dir() and p.logs_dir.is_dir() and p.models_dir.is_dir()
    assert p.free_space_mb() > 0


def test_resolve_helper_prefers_explicit_then_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit" / HELPER_BINARY_NAME
    env_bin = tmp_path / "env" / HELPER_BINARY_NAME
    for b in (explicit, env_bin):
        b.parent.mkdir()
        b.write_text("#!/bin/sh\n")
        b.chmod(b.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("LMR_HELPER_PATH", str(env_bin))
    assert resolve_helper_binary(explicit) == explicit
    assert resolve_helper_binary(None) == env_bin
    not_exec = tmp_path / "noexec"
    not_exec.write_text("x")
    assert resolve_helper_binary(not_exec) == env_bin  # non-executable candidates are skipped


def test_session_store_create_write_read_list(paths: StoragePaths) -> None:
    store = SessionStore(paths)
    older = SessionMetadata(session_id="lmr_20260101T000000_aaaa")
    newer = SessionMetadata(session_id="lmr_20260102T000000_bbbb")
    store.create(older)
    store.create(newer)
    with pytest.raises(FileExistsError):
        store.create(newer)

    newer.mark_saved(duration_s=1.0, video_frames=30)
    store.write(newer)

    assert store.read(newer.session_id).status is SessionStatus.SAVED
    listed = store.list_sessions()
    assert [m.session_id for m in listed] == [newer.session_id, older.session_id]
    assert [m.session_id for m in store.find_incomplete()] == [older.session_id]
    assert store.recording_path(older) == paths.session_dir(older.session_id) / "recording.mov"


def test_session_store_write_is_atomic_and_skips_corrupt(paths: StoragePaths) -> None:
    store = SessionStore(paths)
    meta = SessionMetadata(session_id="lmr_20260101T000000_cccc")
    store.create(meta)
    leftovers = [p for p in store.session_dir(meta.session_id).iterdir() if p.suffix == ".tmp"]
    assert leftovers == []

    corrupt_dir = paths.session_dir("lmr_20260101T000000_dddd")
    corrupt_dir.mkdir()
    (corrupt_dir / "session.json").write_text("{not json", encoding="utf-8")
    (paths.recordings_dir / "stray-file").write_text("ignored")
    ids = [m.session_id for m in store.list_sessions()]
    assert ids == [meta.session_id]

    raw = json.loads(store.session_file(meta.session_id).read_text(encoding="utf-8"))
    assert raw["status"] == "in_progress"
    assert store.session_file(meta.session_id).stat().st_size > 0
