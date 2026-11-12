"""Atomic read/write of ``session.json`` and discovery of incomplete sessions."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from local_meeting_recorder.domain.session_metadata import SessionMetadata, SessionStatus
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.paths import StoragePaths

log = get_logger(__name__)

SESSION_FILE_NAME = "session.json"


class SessionStore:
    def __init__(self, paths: StoragePaths) -> None:
        self._paths = paths

    @property
    def paths(self) -> StoragePaths:
        return self._paths

    def session_dir(self, session_id: str) -> Path:
        """Where this session's files live. Checks every directory that has ever been
        the recordings location (oldest recordings may be under a since-changed
        setting) and returns whichever one actually has it; a session that does not
        exist anywhere yet (i.e. is about to be created) resolves to the current
        default location."""
        for root in self._paths.session_dir_roots():
            candidate = root / session_id
            if (candidate / SESSION_FILE_NAME).is_file():
                return candidate
        return self._paths.session_dir(session_id)

    def session_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / SESSION_FILE_NAME

    def create(self, metadata: SessionMetadata) -> Path:
        """Create the session directory and write the initial metadata. Fails if it exists."""
        directory = self.session_dir(metadata.session_id)
        directory.mkdir(parents=True, exist_ok=False)
        self.write(metadata)
        return directory

    def write(self, metadata: SessionMetadata) -> None:
        """Write ``session.json`` atomically (temp file + rename) so a crash never truncates it."""
        target = self.session_file(metadata.session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = metadata.model_dump_json(indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_name).replace(target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def read(self, session_id: str) -> SessionMetadata:
        return self._read_file(self.session_file(session_id))

    def exists(self, session_id: str) -> bool:
        return self.session_file(session_id).is_file()

    def list_sessions(self) -> list[SessionMetadata]:
        """All readable sessions, newest first, across every directory that has ever
        been the recordings location (``StoragePaths.session_dir_roots()``) — changing
        the recordings directory in Settings never hides sessions saved under a
        previous one. Corrupt files are logged and skipped."""
        sessions: list[SessionMetadata] = []
        for recordings in self._paths.session_dir_roots():
            if not recordings.is_dir():
                continue
            for entry in recordings.iterdir():
                if not entry.is_dir():
                    continue
                session_file = entry / SESSION_FILE_NAME
                if not session_file.is_file():
                    continue
                try:
                    sessions.append(self._read_file(session_file))
                except (OSError, ValueError, ValidationError) as exc:
                    log.warning(
                        "skipping unreadable session file",
                        extra={"path": str(session_file), "error": repr(exc)},
                    )
        sessions.sort(key=lambda m: m.created_at, reverse=True)
        return sessions

    def find_incomplete(self) -> list[SessionMetadata]:
        return [m for m in self.list_sessions() if m.status is SessionStatus.IN_PROGRESS]

    def recording_path(self, metadata: SessionMetadata) -> Path:
        return self.session_dir(metadata.session_id) / metadata.recording_file

    def helper_log_path(self, metadata: SessionMetadata) -> Path:
        return self.session_dir(metadata.session_id) / metadata.helper_log_file

    @staticmethod
    def _read_file(path: Path) -> SessionMetadata:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return SessionMetadata.model_validate(data)
