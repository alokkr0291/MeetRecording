"""Atomic read/write of ``summary.json`` in a session directory.

Same temp-file-then-rename pattern as ``transcription/store.py``. ``append_version``
is the only way callers add a summary — it always reads whatever is already on disk
first, so regenerating with a different provider/model preserves every earlier version
rather than overwriting it (Part F: "preserve previous summaries").
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from local_meeting_recorder.summarization.models import MeetingSummary, SummarySet

SUMMARY_JSON_NAME = "summary.json"
SUMMARY_MARKDOWN_NAME = "summary.md"


class SummaryStore:
    def write(self, session_dir: Path, summary_set: SummarySet) -> Path:
        session_dir.mkdir(parents=True, exist_ok=True)
        json_path = session_dir / SUMMARY_JSON_NAME
        self._atomic_write(json_path, summary_set.model_dump_json(indent=2))
        return json_path

    def write_markdown(self, session_dir: Path, markdown: str) -> Path:
        """Always overwrites with the *latest* summary's rendering — ``summary.json``
        is where every previous version is preserved (``append_version``); the
        Markdown export is a human-readable convenience for the current one only."""
        session_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = session_dir / SUMMARY_MARKDOWN_NAME
        self._atomic_write(markdown_path, markdown)
        return markdown_path

    def read(self, session_dir: Path) -> SummarySet | None:
        json_path = session_dir / SUMMARY_JSON_NAME
        if not json_path.is_file():
            return None
        return SummarySet.model_validate_json(json_path.read_text(encoding="utf-8"))

    def append_version(
        self, session_dir: Path, *, session_id: str, summary: MeetingSummary
    ) -> SummarySet:
        existing = self.read(session_dir)
        versions = [*(existing.versions if existing is not None else []), summary]
        summary_set = SummarySet(session_id=session_id, versions=versions)
        self.write(session_dir, summary_set)
        return summary_set

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".summary-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_name).replace(target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
