"""Shared shape for anything that can turn an audio file into a transcript: the local
``TranscriptionEngine`` (faster-whisper) and the cloud clients in this package.
``TranscriptionManager`` depends only on this protocol, never a concrete provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from local_meeting_recorder.transcription.engine import EngineResult, OnSegment


class TranscriptionProviderClient(Protocol):
    model_name: str

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        on_segment: OnSegment | None = None,
    ) -> EngineResult: ...


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
