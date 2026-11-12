"""Typed application settings.

Precedence (highest first): environment variables with the ``LMR_`` prefix, a ``.env`` file in
the working directory, values from ``config.toml`` in the data directory, built-in defaults.
No setting is secret; the file may be shared freely.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from local_meeting_recorder.storage.paths import default_data_dir

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

CONFIG_FILE_ENV = "LMR_CONFIG_FILE"
CONFIG_FILE_NAME = "config.toml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LMR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage -------------------------------------------------------------------------
    data_dir: Path | None = Field(
        default=None,
        description="Runtime data root. Defaults to the platform Application Support directory.",
    )
    helper_path: Path | None = Field(
        default=None, description="Explicit path to lmr-capture-helper (auto-detected if unset)."
    )
    min_free_disk_mb: int = Field(default=1024, ge=0)

    # Capture -------------------------------------------------------------------------
    display_id: int | None = Field(default=None, description="CGDirectDisplayID; None = main.")
    capture_microphone: bool = True
    video_fps: int = Field(default=30, ge=1, le=60)
    video_bitrate_kbps: int = Field(default=6000, ge=500, le=50_000)
    helper_start_timeout_s: float = Field(default=20.0, gt=0)
    helper_stop_timeout_s: float = Field(default=30.0, gt=0)
    stats_interval_s: float = Field(default=5.0, gt=0)

    # Logging -------------------------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_max_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024)
    log_backup_count: int = Field(default=10, ge=1)

    # Post-processing (ffmpeg found on PATH; Phase 6 may bundle an LGPL build) -------
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    create_mixdown: bool = True

    # Transcription (Phase 4) -----------------------------------------------------------
    # CPU-first by design (Phase 6): device/compute_type are never auto-detected — this
    # app has no GPU-detection code anywhere. "base" is the recommended default model
    # size for an ordinary computer; "tiny" is available for lower-end hardware and
    # "medium"/"large-v3" are advanced, slower options — see settings/profiles.py.
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    @field_validator("data_dir", "helper_path", mode="before")
    @classmethod
    def _expand_user(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
        if isinstance(value, str):
            return None
        return value


def default_config_file(data_dir: Path) -> Path:
    return data_dir / CONFIG_FILE_NAME


def read_config_file(path: Path) -> dict[str, Any]:
    """Load a TOML config file; a missing file is not an error."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a table")
    return data


def load_settings(*, config_file: Path | None = None, env_file: str | None = ".env") -> Settings:
    """Build settings with file values overridden by environment values.

    ``config_file`` defaults to ``$LMR_CONFIG_FILE`` and then ``<data_dir>/config.toml``.
    """
    env_only = Settings(_env_file=env_file)  # type: ignore[call-arg]
    explicit_env = env_only.model_dump(exclude_unset=True)

    if config_file is None:
        env_path = os.environ.get(CONFIG_FILE_ENV)
        data_dir = env_only.data_dir or default_data_dir()
        config_file = Path(env_path).expanduser() if env_path else default_config_file(data_dir)

    file_values = read_config_file(config_file)
    merged = {**file_values, **explicit_env}
    return Settings(_env_file=env_file, **merged)  # type: ignore[call-arg]
