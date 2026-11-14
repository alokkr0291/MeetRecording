from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

import local_meeting_recorder.storage.paths as paths_module
from local_meeting_recorder.domain import SessionMetadata, SessionStatus
from local_meeting_recorder.domain.errors import StorageDirectoryError
from local_meeting_recorder.storage.paths import (
    HELPER_BINARY_NAME,
    StoragePaths,
    default_data_dir,
    resolve_external_binary,
    resolve_helper_binary,
    validate_writable_directory,
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


def test_resolve_helper_dev_build_dir_is_platform_specific(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 8A: a Windows dev build lives under native/windows/build, not native/macos/build —
    selected by ``sys.platform`` at call time, never assumed from the host this test runs
    on. Uses a fake project root so this never touches the real repo tree."""
    win_dev_binary = tmp_path / "native" / "windows" / "build" / "lmr-capture-helper.exe"
    macos_dev_binary = tmp_path / "native" / "macos" / "build" / "lmr-capture-helper"
    for b in (win_dev_binary, macos_dev_binary):
        b.parent.mkdir(parents=True)
        b.write_text("")
        b.chmod(b.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(paths_module, "project_root", lambda: tmp_path)
    monkeypatch.setattr(paths_module, "bundle_contents_dir", lambda: None)
    monkeypatch.setattr(paths_module, "is_frozen", lambda: False)
    monkeypatch.setattr(paths_module.shutil, "which", lambda name: None)

    monkeypatch.setattr(paths_module.sys, "platform", "win32")
    monkeypatch.setattr(paths_module, "HELPER_BINARY_NAME", "lmr-capture-helper.exe")
    assert resolve_helper_binary() == win_dev_binary

    monkeypatch.setattr(paths_module.sys, "platform", "darwin")
    monkeypatch.setattr(paths_module, "HELPER_BINARY_NAME", "lmr-capture-helper")
    assert resolve_helper_binary() == macos_dev_binary


# -- resolve_external_binary (Phase 6C: a double-clicked .app's minimal PATH) -----------


def test_resolve_external_binary_leaves_an_explicit_path_untouched(tmp_path: Path) -> None:
    custom = tmp_path / "custom-ffmpeg"
    assert resolve_external_binary(str(custom)) == str(custom)


def test_resolve_external_binary_prefers_the_current_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths_module.shutil, "which", lambda name: f"/on/path/{name}")
    assert resolve_external_binary("ffmpeg") == "/on/path/ffmpeg"


def test_resolve_external_binary_falls_back_to_common_install_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths_module.shutil, "which", lambda name: None)  # not on the minimal PATH
    fake_homebrew = tmp_path / "opt_homebrew_bin"
    fake_homebrew.mkdir()
    fake_ffmpeg = fake_homebrew / "ffmpeg"
    fake_ffmpeg.write_text("#!/bin/sh\n")
    fake_ffmpeg.chmod(fake_ffmpeg.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(paths_module, "_COMMON_EXTERNAL_BINARY_DIRS", (fake_homebrew,))
    assert resolve_external_binary("ffmpeg") == str(fake_ffmpeg)


def test_resolve_external_binary_returns_the_bare_name_when_nowhere_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(paths_module, "_COMMON_EXTERNAL_BINARY_DIRS", ())
    assert resolve_external_binary("ffmpeg") == "ffmpeg"


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


def test_validate_writable_directory_creates_missing_dir(tmp_path: Path) -> None:
    target = tmp_path / "new" / "nested"
    validate_writable_directory(target)
    assert target.is_dir()


def test_validate_writable_directory_rejects_a_file(tmp_path: Path) -> None:
    target = tmp_path / "a_file"
    target.write_text("x")
    with pytest.raises(StorageDirectoryError):
        validate_writable_directory(target)


def test_validate_writable_directory_rejects_unwritable_dir(tmp_path: Path) -> None:
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(StorageDirectoryError):
            validate_writable_directory(target)
    finally:
        target.chmod(0o700)  # so tmp_path cleanup can remove it


def test_set_recordings_dir_override_changes_recordings_dir(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    new_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(new_dir)
    assert paths.recordings_dir == new_dir


def test_set_recordings_dir_override_keeps_previous_location_in_history(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    default_dir = paths.recordings_dir
    new_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(new_dir)
    assert default_dir in paths.additional_recordings_dirs
    assert paths.session_dir_roots() == [new_dir, default_dir]


def test_set_recordings_dir_override_rejects_unwritable_directory(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with pytest.raises(StorageDirectoryError):
        paths.set_recordings_dir_override(blocked)
    assert paths.recordings_dir_override is None  # rejected change never applied


def test_set_recordings_dir_override_to_none_restores_default_and_keeps_history(
    tmp_path: Path,
) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    default_dir = paths.recordings_dir
    custom_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(custom_dir)
    paths.set_recordings_dir_override(None)
    assert paths.recordings_dir == default_dir
    assert custom_dir in paths.additional_recordings_dirs


def test_sessions_from_a_previous_recordings_dir_remain_listed_after_a_change(
    tmp_path: Path,
) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    paths.ensure()
    store = SessionStore(paths)
    old_session = SessionMetadata(session_id="lmr_20260101T000000_aaaa")
    store.create(old_session)

    new_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(new_dir)
    paths.ensure()
    new_session = SessionMetadata(session_id="lmr_20260102T000000_bbbb")
    store.create(new_session)

    listed_ids = {m.session_id for m in store.list_sessions()}
    assert listed_ids == {old_session.session_id, new_session.session_id}


def test_session_dir_resolves_existing_session_under_a_historical_root(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    paths.ensure()
    store = SessionStore(paths)
    old_session = SessionMetadata(session_id="lmr_20260101T000000_cccc")
    old_dir = store.create(old_session)

    new_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(new_dir)

    # the session still resolves to where it actually lives, not the new default root
    assert store.session_dir(old_session.session_id) == old_dir
    assert store.read(old_session.session_id).session_id == old_session.session_id
    assert store.recording_path(old_session) == old_dir / "recording.mov"


def test_session_dir_for_a_brand_new_session_uses_the_current_default(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    paths.ensure()
    store = SessionStore(paths)
    new_dir = tmp_path / "custom-recordings"
    paths.set_recordings_dir_override(new_dir)

    assert store.session_dir("lmr_brand_new_session") == new_dir / "lmr_brand_new_session"
