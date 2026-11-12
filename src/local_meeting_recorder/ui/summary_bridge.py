"""Re-emits ``SummaryManager`` callbacks as Qt signals.

Same pattern as ``transcription_bridge.py``: the manager's callbacks fire on its own
background thread (one per session job), and this class only turns them into signals —
Qt's queued connections deliver them safely onto the GUI thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from local_meeting_recorder.summarization.manager import SummaryCallbacks


class SummaryBridge(QObject):
    started = Signal(str)  # session_id
    completed = Signal(str, object)  # session_id, MeetingSummary
    failed = Signal(str, str, str)  # session_id, code, message

    @property
    def callbacks(self) -> SummaryCallbacks:
        """Pass this to ``SummaryManager(callbacks=...)``."""
        return SummaryCallbacks(
            on_started=self.started.emit,
            on_completed=self.completed.emit,
            on_failed=self.failed.emit,
        )
