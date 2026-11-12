"""Local speech-to-text via ``faster-whisper``.

The model loads lazily: constructing ``TranscriptionEngine`` touches neither disk nor
network, and the underlying ``WhisperModel`` (which may download weights on first use) is
only created the first time ``transcribe()`` is called. Nothing here imports or runs on
the UI thread — callers (``transcription/manager.py``) are responsible for running this
on a background thread.

Phase 6 (CPU-first readiness): ``device``/``compute_type`` default to CPU + int8 and are
never auto-detected — this app has no GPU-detection code anywhere, for faster-whisper or
otherwise. ``cpu_threads``/``num_workers`` (``0`` = ctranslate2's own default) let a "Low
Resource" processing profile (``settings/profiles.py``) cap how many threads a single
transcription job uses, so it doesn't claim every core on an entry-level machine.
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from local_meeting_recorder.domain.errors import TranscriptionEmptyAudioError, TranscriptionError
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.transcription.models import TranscriptSegment

log = get_logger(__name__)

DEFAULT_MODEL_SIZE = "base"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"

# (processed_s, total_s) — total_s is None if the model could not report a duration.
OnSegment = Callable[[float, float | None], None]


@dataclass(frozen=True)
class EngineResult:
    language: str
    language_probability: float | None
    duration_s: float
    segments: list[TranscriptSegment]


class TranscriptionEngine:
    """Wraps one ``faster_whisper.WhisperModel``, built on first use."""

    def __init__(
        self,
        *,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        download_root: Path | None = None,
        cpu_threads: int = 0,
    ) -> None:
        self.model_size = model_size
        self.model_name = model_size  # satisfies TranscriptionProviderClient's shared shape
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self._cpu_threads = cpu_threads
        self._model: WhisperModel | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        on_segment: OnSegment | None = None,
    ) -> EngineResult:
        """Transcribe ``audio_path`` (expected mono 16 kHz PCM WAV).

        ``language`` is an explicit two-letter code (e.g. ``"en"``), or ``None`` for
        automatic detection. Raises ``TranscriptionEmptyAudioError`` if the file has no
        audio frames (checked before the model is even loaded), and ``TranscriptionError``
        for any model load or inference failure.
        """
        self._check_not_empty(audio_path)
        model = self._load_model()
        try:
            raw_segments, info = model.transcribe(str(audio_path), language=language)
            segments: list[TranscriptSegment] = []
            for raw in raw_segments:
                segments.append(
                    TranscriptSegment(start_s=raw.start, end_s=raw.end, text=raw.text.strip())
                )
                if on_segment is not None:
                    on_segment(raw.end, info.duration or None)
        except Exception as exc:
            raise TranscriptionError(
                f"faster-whisper failed to transcribe {audio_path}: {exc}"
            ) from exc
        return EngineResult(
            language=info.language,
            language_probability=info.language_probability,
            duration_s=info.duration,
            segments=segments,
        )

    def _load_model(self) -> WhisperModel:
        if self._model is None:
            log.info(
                "loading whisper model",
                extra={"model_size": self.model_size, "device": self._device},
            )
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=str(self._download_root) if self._download_root else None,
                    cpu_threads=self._cpu_threads,
                )
            except Exception as exc:
                raise TranscriptionError(
                    f"failed to load whisper model '{self.model_size}': {exc}"
                ) from exc
        return self._model

    @staticmethod
    def _check_not_empty(audio_path: Path) -> None:
        try:
            with wave.open(str(audio_path), "rb") as wav_file:
                frames = wav_file.getnframes()
        except (OSError, wave.Error) as exc:
            raise TranscriptionError(f"{audio_path} is not a valid WAV file: {exc}") from exc
        if frames == 0:
            raise TranscriptionEmptyAudioError(f"{audio_path} contains no audio frames")
