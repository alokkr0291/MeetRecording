from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_recorder.config import Settings, load_settings, read_config_file


def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.data_dir is None
    assert s.min_free_disk_mb == 1024
    assert s.video_fps == 30
    assert s.capture_microphone is True
    assert s.log_level == "INFO"
    assert s.ollama_base_url.startswith("http://127.0.0.1")


def test_env_prefix_and_path_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMR_DATA_DIR", "~/lmr-test-data")
    monkeypatch.setenv("LMR_VIDEO_FPS", "15")
    monkeypatch.setenv("LMR_CAPTURE_MICROPHONE", "false")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.data_dir == Path.home() / "lmr-test-data"
    assert s.video_fps == 15
    assert s.capture_microphone is False


def test_empty_env_path_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMR_HELPER_PATH", "")
    assert Settings(_env_file=None).helper_path is None  # type: ignore[call-arg]


def test_validation_rejects_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMR_VIDEO_FPS", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setenv("LMR_VIDEO_FPS", "30")
    monkeypatch.setenv("LMR_LOG_LEVEL", "LOUD")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_config_file_is_overridden_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('video_fps = 10\nmin_free_disk_mb = 42\nlog_level = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("LMR_VIDEO_FPS", "24")
    s = load_settings(config_file=cfg, env_file=None)
    assert s.video_fps == 24  # env wins
    assert s.min_free_disk_mb == 42  # file applies
    assert s.log_level == "DEBUG"


def test_config_file_env_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "elsewhere.toml"
    cfg.write_text("video_bitrate_kbps = 2500\n", encoding="utf-8")
    monkeypatch.setenv("LMR_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("LMR_DATA_DIR", str(tmp_path / "data"))
    s = load_settings(env_file=None)
    assert s.video_bitrate_kbps == 2500
    assert s.data_dir == tmp_path / "data"


def test_missing_config_file_is_fine(tmp_path: Path) -> None:
    assert read_config_file(tmp_path / "nope.toml") == {}
    s = load_settings(config_file=tmp_path / "nope.toml", env_file=None)
    assert s.video_fps == 30
