"""Where runtime data lives and how the native helper binary is located."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from local_meeting_recorder import APP_NAME

HELPER_BINARY_NAME = "lmr-capture-helper"
HELPER_PATH_ENV = "LMR_HELPER_PATH"


def platform_name() -> str:
    """``sys.platform`` behind a function so type checkers do not prune other branches."""
    return sys.platform


def default_data_dir() -> Path:
    platform = platform_name()
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_NAME


def project_root() -> Path:
    """Repository root in development (``src/local_meeting_recorder/storage/paths.py`` -> root)."""
    return Path(__file__).resolve().parents[3]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_contents_dir() -> Path | None:
    """``LocalMeetingRecorder.app/Contents`` when running from a PyInstaller bundle."""
    if not is_frozen():
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.name == "Contents":
            return parent
    return None


def resolve_helper_binary(explicit: Path | None = None) -> Path | None:
    """Locate the capture helper: explicit setting, env var, app bundle, dev build dir, PATH."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env_value = os.environ.get(HELPER_PATH_ENV)
    if env_value:
        candidates.append(Path(env_value).expanduser())
    contents = bundle_contents_dir()
    if contents is not None:
        candidates.append(contents / "Helpers" / HELPER_BINARY_NAME)
    candidates.append(project_root() / "native" / "macos" / "build" / HELPER_BINARY_NAME)
    on_path = shutil.which(HELPER_BINARY_NAME)
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


@dataclass(frozen=True)
class StoragePaths:
    root: Path

    @classmethod
    def from_settings_dir(cls, data_dir: Path | None) -> StoragePaths:
        return cls(root=(data_dir or default_data_dir()).expanduser())

    @property
    def recordings_dir(self) -> Path:
        return self.root / "recordings"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def database_path(self) -> Path:
        return self.root / "library.sqlite"

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    def session_dir(self, session_id: str) -> Path:
        return self.recordings_dir / session_id

    def ensure(self) -> None:
        for directory in (self.root, self.recordings_dir, self.logs_dir, self.models_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def free_space_mb(self) -> float:
        probe = self.recordings_dir if self.recordings_dir.exists() else self.root
        if not probe.exists():
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        return usage.free / (1024 * 1024)
