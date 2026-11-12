"""SettingsService: defaults, atomic persistence, validation, and applying a persisted
recordings directory at startup."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_meeting_recorder.domain.errors import StorageDirectoryError
from local_meeting_recorder.settings.models import AppSettings, TranscriptionProviderId
from local_meeting_recorder.settings.service import SettingsService, apply_recordings_dir_override
from local_meeting_recorder.storage.paths import StoragePaths


def test_default_settings_when_no_file_exists(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    assert service.current == AppSettings()
    assert service.current.transcription_provider is TranscriptionProviderId.LOCAL_WHISPER
    assert service.current.recordings_dir is None


def test_update_persists_and_is_reloaded_by_a_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    service.update(whisper_model="tiny")

    reloaded = SettingsService(path)
    assert reloaded.current.whisper_model == "tiny"


def test_update_returns_and_stores_the_merged_settings(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    updated = service.update(transcription_provider=TranscriptionProviderId.OPENAI)
    assert updated.transcription_provider is TranscriptionProviderId.OPENAI
    assert service.current.transcription_provider is TranscriptionProviderId.OPENAI
    # unrelated fields are untouched by a partial update
    assert service.current.whisper_model == "base"


def test_update_with_invalid_value_raises_and_does_not_persist(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    with pytest.raises(ValidationError):
        service.update(transcription_provider="not_a_real_provider")
    assert service.current.transcription_provider is TranscriptionProviderId.LOCAL_WHISPER
    reloaded = SettingsService(path)
    assert reloaded.current.transcription_provider is TranscriptionProviderId.LOCAL_WHISPER


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not valid json", encoding="utf-8")
    service = SettingsService(path)
    assert service.current == AppSettings()


def test_invalid_schema_in_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"transcription_provider": "bogus"}', encoding="utf-8")
    service = SettingsService(path)
    assert service.current == AppSettings()


def test_write_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    service.update(whisper_model="tiny")
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".settings-")]
    assert leftovers == []


def test_apply_recordings_dir_override_noop_when_unset(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    error = apply_recordings_dir_override(paths, AppSettings())
    assert error is None
    assert paths.recordings_dir_override is None


def test_apply_recordings_dir_override_sets_a_valid_directory(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    chosen = tmp_path / "my-recordings"
    error = apply_recordings_dir_override(paths, AppSettings(recordings_dir=chosen))
    assert error is None
    assert paths.recordings_dir == chosen


def test_apply_recordings_dir_override_reports_an_unavailable_directory(tmp_path: Path) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    # a file, not a directory, so mkdir/validate fails
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    error = apply_recordings_dir_override(paths, AppSettings(recordings_dir=blocked))
    assert error is not None
    assert paths.recordings_dir_override is None  # falls back to the default, does not crash


def test_apply_recordings_dir_override_raises_type_is_storage_directory_error(
    tmp_path: Path,
) -> None:
    paths = StoragePaths(root=tmp_path / "data")
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    with pytest.raises(StorageDirectoryError):
        paths.set_recordings_dir_override(blocked)
