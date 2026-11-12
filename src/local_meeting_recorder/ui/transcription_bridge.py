"""Re-emits ``TranscriptionManager`` callbacks as Qt signals.

Same pattern as ``coordinator_bridge.py``: the manager's callbacks fire on its own
background thread (one per session job), and this class only turns them into signals —
Qt's queued connections deliver them safely onto the GUI thread. Unlike
``CoordinatorBridge``, there is no two-way ``attach()``: ``AppController`` holds its own
reference to the ``TranscriptionManager`` to call ``transcribe_async`` directly (it does
its own background threading and returns immediately), so this bridge only ever needs to
go one way, manager -> UI.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from local_meeting_recorder.transcription.manager import TranscriptionCallbacks


class TranscriptionBridge(QObject):
    started = Signal(str)  # session_id
    progress = Signal(object)  # TranscriptionProgress
    completed = Signal(str, object)  # session_id, Transcript
    failed = Signal(str, str, str)  # session_id, code, message
    diarization_started = Signal(str)  # session_id
    diarization_completed = Signal(str, object)  # session_id, Transcript
    diarization_failed = Signal(str, str, str)  # session_id, code, message

    @property
    def callbacks(self) -> TranscriptionCallbacks:
        """Pass this to ``TranscriptionManager(callbacks=...)``."""
        return TranscriptionCallbacks(
            on_started=self.started.emit,
            on_progress=self.progress.emit,
            on_completed=self.completed.emit,
            on_failed=self.failed.emit,
            on_diarization_started=self.diarization_started.emit,
            on_diarization_completed=self.diarization_completed.emit,
            on_diarization_failed=self.diarization_failed.emit,
        )
