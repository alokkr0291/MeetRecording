"""Extracts mono 16 kHz PCM WAV audio from a finished recording via ``ffmpeg``, for
transcription.

Read-only on the source: this only ever runs ``ffmpeg -i <source> ... <dest>``, never
touching, truncating, or moving the original recording. The extracted WAV is meant to be
disposable — callers typically write it into a temporary directory and discard it once
transcription finishes (see ``transcription/manager.py``).
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from local_meeting_recorder.domain.errors import (
    AudioExtractionError,
    MediaError,
    NoAudioStreamError,
)
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.media.inspector import MediaInspector, StreamSummary
from local_meeting_recorder.storage.paths import resolve_external_binary

log = get_logger(__name__)

TRANSCRIPTION_SAMPLE_RATE_HZ = 16_000
TRANSCRIPTION_CHANNELS = 1


class AudioExtractor:
    """Extracts audio from a finished recording: either every track mixed down to one
    mono 16 kHz PCM WAV file (``extract_wav16k_mono``, Phase 4's single-transcript
    pipeline), or one specific track in isolation (``extract_track_wav16k_mono``, Phase
    5's source-separated pipeline — see ``transcription/manager.py``)."""

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        timeout: float = 300.0,
    ) -> None:
        # Resolved, not used as-is: a packaged .app launched by double-clicking it gets
        # a minimal PATH that excludes Homebrew — see resolve_external_binary()'s
        # docstring. A dev run's shell PATH (or an explicit absolute-path override)
        # already works today and is left untouched by this resolution.
        self._ffmpeg = resolve_external_binary(ffmpeg_path)
        self._timeout = timeout
        self._inspector = MediaInspector(
            ffprobe_path=resolve_external_binary(ffprobe_path),
            ffmpeg_path=self._ffmpeg,
            timeout=timeout,
        )

    def extract_wav16k_mono(self, source: Path, dest: Path) -> Path:
        """Mix down every audio stream in ``source`` into ``dest`` (mono, 16 kHz, PCM16).

        Raises ``NoAudioStreamError`` if ``source`` has no audio streams at all,
        ``AudioExtractionError`` if ffmpeg fails or produces unusable output, and
        ``MediaError`` if ``source`` itself cannot be probed (missing file, corrupt
        container).
        """
        audio_streams = self._probe_audio_streams(source)
        self._run_ffmpeg(source, dest, self._downmix_args(len(audio_streams)))
        self._validate(dest)
        return dest

    def extract_track_wav16k_mono(self, source: Path, dest: Path, *, stream_index: int) -> Path:
        """Extract exactly one audio stream (by its ffprobe stream ``index``, e.g. the
        ``index`` recorded in a session's ``audio_tracks``) from ``source`` into ``dest``
        (mono, 16 kHz, PCM16) — no mixing with any other track.

        Raises ``NoAudioStreamError`` if ``source`` has no audio stream with that index,
        ``AudioExtractionError``/``MediaError`` as ``extract_wav16k_mono`` does.
        """
        audio_streams = self._probe_audio_streams(source)
        if not any(s.index == stream_index for s in audio_streams):
            raise NoAudioStreamError(f"{source} has no audio stream with index {stream_index}")
        self._run_ffmpeg(source, dest, ["-map", f"0:{stream_index}"])
        self._validate(dest)
        return dest

    def _probe_audio_streams(self, source: Path) -> list[StreamSummary]:
        if not source.is_file():
            raise MediaError(f"recording not found: {source}")
        summary = self._inspector.probe(source)
        audio_streams = summary.audio_streams
        if not audio_streams:
            raise NoAudioStreamError(f"{source} has no audio streams")
        return audio_streams

    def _run_ffmpeg(self, source: Path, dest: Path, map_args: list[str]) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            self._ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            *map_args,
            "-ac",
            str(TRANSCRIPTION_CHANNELS),
            "-ar",
            str(TRANSCRIPTION_SAMPLE_RATE_HZ),
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            str(dest),
        ]
        try:
            subprocess.run(argv, capture_output=True, text=True, timeout=self._timeout, check=True)
        except FileNotFoundError as exc:
            raise AudioExtractionError(f"{self._ffmpeg} not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioExtractionError(f"ffmpeg timed out extracting audio from {source}") from exc
        except subprocess.CalledProcessError as exc:
            raise AudioExtractionError(
                f"ffmpeg failed extracting audio from {source}: {exc.stderr.strip()}"
            ) from exc

    @staticmethod
    def _downmix_args(stream_count: int) -> list[str]:
        if stream_count == 1:
            return ["-map", "0:a:0"]
        inputs = "".join(f"[0:a:{i}]" for i in range(stream_count))
        filter_complex = (
            f"{inputs}amix=inputs={stream_count}:duration=longest:dropout_transition=2[aout]"
        )
        return ["-filter_complex", filter_complex, "-map", "[aout]"]

    @staticmethod
    def _validate(dest: Path) -> None:
        if not dest.is_file() or dest.stat().st_size == 0:
            raise AudioExtractionError(f"ffmpeg produced no output at {dest}")
        try:
            with wave.open(str(dest), "rb") as wav_file:
                channels = wav_file.getnchannels()
                rate = wav_file.getframerate()
                frames = wav_file.getnframes()
        except (OSError, wave.Error) as exc:
            raise AudioExtractionError(
                f"extracted audio at {dest} is not a valid WAV file"
            ) from exc
        if channels != TRANSCRIPTION_CHANNELS or rate != TRANSCRIPTION_SAMPLE_RATE_HZ:
            raise AudioExtractionError(
                f"extracted audio at {dest} has unexpected format: "
                f"{channels}ch @ {rate}Hz (expected "
                f"{TRANSCRIPTION_CHANNELS}ch @ {TRANSCRIPTION_SAMPLE_RATE_HZ}Hz)"
            )
        if frames == 0:
            raise AudioExtractionError(f"extracted audio at {dest} contains zero frames")
