"""``SettingsService``: atomic load/save of ``AppSettings`` to a JSON file.

Same temp-file-then-rename pattern as ``storage/session_store.py``. Pure Python — no Qt
import anywhere in this module — so it is independent of whatever UI framework reads or
changes it, and is trivially unit-testable with a ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from local_meeting_recorder.domain.errors import StorageDirectoryError
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.settings.models import APP_SETTINGS_SCHEMA_VERSION, AppSettings
from local_meeting_recorder.storage.paths import StoragePaths

log = get_logger(__name__)

SETTINGS_FILE_NAME = "settings.json"


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an on-disk settings dict to the current schema version.

    Only version 1 exists today, so this is a no-op placeholder; the next time the
    schema changes, add an ``if version == N:`` branch here that rewrites old field
    names/shapes forward, mirroring ``domain/session_metadata.py``'s
    ``SESSION_SCHEMA_VERSION`` convention. An unrecognized version is logged and left
    as-is — ``AppSettings.model_validate`` will fall back to defaults for anything it
    cannot parse rather than crash.
    """
    version = data.get("schema_version", APP_SETTINGS_SCHEMA_VERSION)
    if version != APP_SETTINGS_SCHEMA_VERSION:
        log.warning("settings file has an unrecognized schema_version", extra={"version": version})
    return data


class SettingsService:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._settings = self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def current(self) -> AppSettings:
        return self._settings

    def update(self, **changes: Any) -> AppSettings:
        """Apply ``changes`` to the current settings, validate, persist, and return the
        new settings. Raises ``pydantic.ValidationError`` and writes nothing if a change
        is invalid — the previously persisted settings are untouched."""
        merged = {**self._settings.model_dump(mode="json"), **changes}
        updated = AppSettings.model_validate(merged)
        self._write(updated)
        self._settings = updated
        return updated

    def _load(self) -> AppSettings:
        if not self._path.is_file():
            return AppSettings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning(
                "settings file unreadable, using defaults",
                extra={"path": str(self._path), "error": repr(exc)},
            )
            return AppSettings()
        raw = _migrate(raw)
        try:
            return AppSettings.model_validate(raw)
        except ValidationError as exc:
            log.warning(
                "settings file invalid, using defaults",
                extra={"path": str(self._path), "error": repr(exc)},
            )
            return AppSettings()

    def _write(self, settings: AppSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump_json(indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_name).replace(self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise


def apply_recordings_dir_override(paths: StoragePaths, settings: AppSettings) -> str | None:
    """Apply a persisted recordings-directory choice to ``paths`` at startup.

    Returns ``None`` on success (including "nothing to apply"), or a human-readable
    error message if the saved directory is no longer usable (e.g. an external drive
    that is not mounted right now) — the caller should show this to the user rather
    than crash; ``paths`` keeps using its default location for new recordings either
    way, and nothing already on disk is affected.
    """
    if settings.recordings_dir is None:
        return None
    try:
        paths.set_recordings_dir_override(settings.recordings_dir)
    except StorageDirectoryError as exc:
        log.warning(
            "persisted recordings directory is unavailable; using the default instead",
            extra={"path": str(settings.recordings_dir), "error": repr(exc)},
        )
        return str(exc)
    return None
