"""Where runtime data lives and how the native helper binary is located."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from local_meeting_recorder import APP_NAME
from local_meeting_recorder.domain.errors import StorageDirectoryError

HELPER_BINARY_NAME = "lmr-capture-helper.exe" if sys.platform == "win32" else "lmr-capture-helper"
HELPER_PATH_ENV = "LMR_HELPER_PATH"


def validate_writable_directory(path: Path) -> None:
    """Raise ``StorageDirectoryError`` unless ``path`` exists (or can be created) and is
    writable. Never silently accepts a directory it cannot actually use."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageDirectoryError(f"cannot create directory {path}: {exc}") from exc
    if not path.is_dir():
        raise StorageDirectoryError(f"{path} exists but is not a directory")
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".lmr-write-test-", dir=path)
        os.close(fd)
        Path(tmp_name).unlink()
    except OSError as exc:
        raise StorageDirectoryError(f"{path} is not writable: {exc}") from exc


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
    """The ``.app``'s ``Contents`` dir when running from a PyInstaller bundle.

    Matched by directory name only, never the bundle's own display/file name — this must
    keep working across a rebrand that renames the ``.app`` on disk (e.g. "My Recorder.app").
    """
    if not is_frozen():
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.name == "Contents":
            return parent
    return None


#: Common install locations for CLI tools this app shells out to but does not bundle
#: (ffmpeg/ffprobe). A GUI app launched by double-clicking it (LaunchServices) gets a
#: minimal PATH that does NOT source the user's shell profile — confirmed on this
#: machine: `/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.`, which excludes Homebrew's
#: Apple Silicon prefix. A terminal-launched dev run already has these on PATH via the
#: user's shell, so `shutil.which()` finds them there first; this list only matters for
#: the double-click-from-Finder case these constants exist to fix.
_COMMON_EXTERNAL_BINARY_DIRS: tuple[Path, ...] = (
    Path("/opt/homebrew/bin"),  # Homebrew, Apple Silicon
    Path("/usr/local/bin"),  # Homebrew, Intel (and many manual installs)
    Path("/opt/local/bin"),  # MacPorts
)


def resolve_external_binary(name_or_path: str) -> str:
    """Best-effort absolute path to an external CLI tool (``ffmpeg``/``ffprobe``) this
    app depends on but never bundles. An already-explicit path (contains a path
    separator) is returned unchanged — never second-guess a deliberate override. A bare
    command name is checked against the current ``PATH`` first, then the common install
    locations above. Falls back to ``name_or_path`` unchanged if nothing is found, so
    the eventual subprocess failure is still a plain, clear "command not found" rather
    than a path this function invented.
    """
    if os.sep in name_or_path or (os.altsep and os.altsep in name_or_path):
        return name_or_path
    on_path = shutil.which(name_or_path)
    if on_path:
        return on_path
    for directory in _COMMON_EXTERNAL_BINARY_DIRS:
        candidate = directory / name_or_path
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return name_or_path


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
    elif sys.platform == "win32" and is_frozen():
        # No Windows packaging script exists yet (Phase 8A is capture-only) — this mirrors
        # the macOS `Contents/Helpers/` convention for whenever one is built, and is inert
        # until that directory actually exists.
        candidates.append(Path(sys.executable).resolve().parent / "Helpers" / HELPER_BINARY_NAME)
    dev_native_dir = "windows" if sys.platform == "win32" else "macos"
    candidates.append(project_root() / "native" / dev_native_dir / "build" / HELPER_BINARY_NAME)
    on_path = shutil.which(HELPER_BINARY_NAME)
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


@dataclass
class StoragePaths:
    """Not frozen: ``recordings_dir_override`` can change at runtime (the Settings
    window), taking effect for the next recording without an app restart.
    ``additional_recordings_dirs`` accumulates every directory that was ever the active
    recordings location — changing the setting never hides previously saved recordings;
    ``session_dir_roots()`` is the union every caller that lists sessions should scan.
    """

    root: Path
    recordings_dir_override: Path | None = None
    additional_recordings_dirs: list[Path] = field(default_factory=list)

    @classmethod
    def from_settings_dir(cls, data_dir: Path | None) -> StoragePaths:
        return cls(root=(data_dir or default_data_dir()).expanduser())

    @property
    def recordings_dir(self) -> Path:
        return self.recordings_dir_override or (self.root / "recordings")

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

    def session_dir_roots(self) -> list[Path]:
        """Every directory that has ever held recordings: the current one first, then
        every previous one, de-duplicated. Use this to list sessions; use
        ``recordings_dir`` only to decide where a *new* session goes."""
        seen: list[Path] = [self.recordings_dir]
        for directory in self.additional_recordings_dirs:
            if directory not in seen:
                seen.append(directory)
        return seen

    def set_recordings_dir_override(self, new_dir: Path | None) -> None:
        """Change where new recordings go (``new_dir=None`` restores the default).
        Validates ``new_dir`` first (exists-or-creatable, writable) and never moves or
        deletes anything already on disk — the previous location, whichever it was, is
        kept in ``additional_recordings_dirs`` so it stays visible."""
        if new_dir is not None:
            validate_writable_directory(new_dir)
        previous = self.recordings_dir
        self.recordings_dir_override = new_dir
        if previous != self.recordings_dir and previous not in self.additional_recordings_dirs:
            self.additional_recordings_dirs.append(previous)

    def ensure(self) -> None:
        for directory in (self.root, self.recordings_dir, self.logs_dir, self.models_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def free_space_mb(self) -> float:
        probe = self.recordings_dir if self.recordings_dir.exists() else self.root
        if not probe.exists():
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        return usage.free / (1024 * 1024)
