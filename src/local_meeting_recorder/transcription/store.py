"""Atomic read/write of ``transcript.json``/``transcript.txt`` in a session directory.

Same temp-file-then-rename pattern as ``storage/session_store.py``, so a crash mid-write
can never leave a truncated or half-written transcript on disk.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from local_meeting_recorder.transcription.models import Transcript

TRANSCRIPT_JSON_NAME = "transcript.json"
TRANSCRIPT_TXT_NAME = "transcript.txt"


@dataclass(frozen=True)
class TranscriptPaths:
    json_path: Path
    txt_path: Path


class TranscriptStore:
    def write(self, session_dir: Path, transcript: Transcript) -> TranscriptPaths:
        session_dir.mkdir(parents=True, exist_ok=True)
        json_path = session_dir / TRANSCRIPT_JSON_NAME
        txt_path = session_dir / TRANSCRIPT_TXT_NAME
        self._atomic_write(json_path, transcript.model_dump_json(indent=2))
        self._atomic_write(txt_path, transcript.text)
        return TranscriptPaths(json_path=json_path, txt_path=txt_path)

    def read(self, session_dir: Path) -> Transcript:
        json_path = session_dir / TRANSCRIPT_JSON_NAME
        return Transcript.model_validate_json(json_path.read_text(encoding="utf-8"))

    def exists(self, session_dir: Path) -> bool:
        return (session_dir / TRANSCRIPT_JSON_NAME).is_file()

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_name).replace(target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
