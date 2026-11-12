"""Shared shape for any diarization backend. Only one implementation exists today
(``pyannote_engine.py``), but keeping this provider-independent means a future
lighter-weight or GPU-accelerated backend can be swapped in without touching
``speaker/assignment.py`` or ``transcription/manager.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SpeakerRegion:
    """One contiguous stretch of time a diarization backend attributes to a single
    voice. ``speaker_label`` is whatever raw label the backend produces (e.g.
    ``"SPEAKER_00"``) — stable only within one call, remapped to a cleaner
    ``"speaker_1"``/``"speaker_2"`` numbering by ``speaker/assignment.py``."""

    start_s: float
    end_s: float
    speaker_label: str


@dataclass(frozen=True)
class DiarizationResult:
    regions: list[SpeakerRegion]
    speaker_count: int
    model_name: str


class SpeakerDiarizationEngine(Protocol):
    def diarize(self, audio_path: Path) -> DiarizationResult:
        """Identify speaker regions in ``audio_path`` (mono 16 kHz WAV, matching
        ``AudioExtractor``'s output). Raises ``DiarizationError`` (or a subclass) on any
        failure — never returns a fabricated result."""
        ...
