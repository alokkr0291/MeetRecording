"""Shared HTTP plumbing for the OpenAI/OpenRouter transcription clients.

Both expose an OpenAI-compatible ``multipart/form-data`` transcription endpoint and a
cheap bearer-token-validation endpoint; the only differences are the URLs, which extra
form fields request segment timestamps, and how a valid-key response is recognized. Each
subclass sets those via class attributes and the two small hooks below.

Not every model supports segment timestamps: OpenAI's own docs confirm
``response_format=verbose_json`` (the only way to get per-segment start/end times) is
supported by ``whisper-1`` but **not** by ``gpt-4o-transcribe``/``gpt-4o-mini-transcribe``,
which only support plain ``json`` (a single ``text`` field, no timestamps, no language).
``segment_capable_models`` (a subclass class attribute) controls which models get
``verbose_json``; a model not in that set still transcribes correctly, just without
per-segment timestamps — handled by ``_parse_transcription_response`` below rather than
assumed to always be present.

``opener`` is injectable (defaults to ``urllib.request.urlopen``) so tests can supply a
fake HTTP transport — no real network calls, no real API keys, in the automated suite.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from local_meeting_recorder.domain.errors import TranscriptionError, sanitize_error_detail
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.transcription.engine import EngineResult, OnSegment
from local_meeting_recorder.transcription.models import TranscriptSegment
from local_meeting_recorder.transcription.providers.http_encoding import encode_multipart
from local_meeting_recorder.transcription.providers.protocol import ConnectionTestResult

log = get_logger(__name__)

UrlOpener = Callable[..., Any]  # matches urllib.request.urlopen's (request, timeout=...) shape

# OpenAI's documented hard cap for /v1/audio/transcriptions is exactly 25 MiB
# (26,214,400 bytes) measured against the whole request body — confirmed from a real
# 413 error hit by a long meeting recording: "Maximum content size limit (26214400)
# exceeded (26385366 bytes read)" (the ~170KB over the raw file size is multipart
# boundary/header overhead, see http_encoding.py). For a 16kHz mono 16-bit WAV (this
# app's extraction format, media/audio_extractor.py), that cap is hit at ~13.6 minutes
# per source track — well within a normal meeting length, and every long-recording
# transcription with a cloud provider failed outright before this fix. OpenRouter's own
# limit for its OpenAI-compatible endpoint is not independently confirmed, so the same
# conservative threshold applies to both clients sharing this base class: splitting a
# file that didn't strictly need it is harmless, silently failing on one that did is not.
_CHUNK_TARGET_BYTES = 20 * 1024 * 1024  # ~20% margin below the 25 MiB hard cap


def _local_wav_duration_s(audio_path: Path) -> float:
    """Best-effort duration of the local (always-WAV) file, used as a fallback when the
    provider's response does not report one (the plain ``json`` format never does)."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            rate = wav_file.getframerate()
            return wav_file.getnframes() / rate if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def _wav_chunk_windows(audio_path: Path, *, max_bytes: int) -> list[tuple[float, float]]:
    """``(start_s, end_s)`` windows covering the whole file, each window's raw PCM
    payload at or under ``max_bytes``. Returns ``[]`` if the file cannot be read as a
    WAV — the caller falls back to a single upload, whose own HTTP error (if any)
    surfaces normally rather than this function inventing a diagnosis."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sampwidth = wav_file.getsampwidth()
            total_frames = wav_file.getnframes()
    except (OSError, wave.Error):
        return []
    if rate <= 0 or total_frames <= 0 or channels <= 0 or sampwidth <= 0:
        return []
    frames_per_chunk = max(1, min(total_frames, max_bytes // (channels * sampwidth)))
    windows: list[tuple[float, float]] = []
    start_frame = 0
    while start_frame < total_frames:
        end_frame = min(start_frame + frames_per_chunk, total_frames)
        windows.append((start_frame / rate, end_frame / rate))
        start_frame = end_frame
    return windows


def _write_wav_window(src_path: Path, dest_path: Path, *, start_s: float, end_s: float) -> None:
    """Copies one time window of ``src_path`` into a new, standalone WAV file — no
    re-encoding, since the source is already PCM and all this needs to do is split it."""
    with wave.open(str(src_path), "rb") as src:
        rate = src.getframerate()
        channels = src.getnchannels()
        sampwidth = src.getsampwidth()
        src.setpos(int(start_s * rate))
        frames = src.readframes(int(end_s * rate) - int(start_s * rate))
    with wave.open(str(dest_path), "wb") as dest:
        dest.setnchannels(channels)
        dest.setsampwidth(sampwidth)
        dest.setframerate(rate)
        dest.writeframes(frames)


class HttpTranscriptionClientBase:
    """Not used directly — ``OpenAITranscriptionClient``/``OpenRouterTranscriptionClient``
    set the class attributes below and inherit this."""

    provider_label = "cloud provider"
    transcribe_url = ""
    key_test_url = ""
    default_model = ""
    extra_transcribe_fields: ClassVar[dict[str, str]] = {}
    # None = every model this client can be pointed at supports verbose_json/segment
    # timestamps; a set = only those model names do (others fall back to plain text).
    segment_capable_models: ClassVar[set[str] | None] = None

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        timeout: float = 120.0,
        opener: UrlOpener = urllib.request.urlopen,
        chunk_target_bytes: int = _CHUNK_TARGET_BYTES,
    ) -> None:
        # Instance (not class) attribute so tests can exercise chunking without a real
        # multi-megabyte fixture, by passing a tiny value here.
        self.chunk_target_bytes = chunk_target_bytes
        self.model_name = model or self.default_model
        self._api_key = api_key
        self._timeout = timeout
        self._opener = opener

    def _response_format(self) -> str:
        if self.segment_capable_models is None or self.model_name in self.segment_capable_models:
            return "verbose_json"
        return "json"

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        on_segment: OnSegment | None = None,
    ) -> EngineResult:
        """A file under ``chunk_target_bytes`` is sent as one request (unchanged
        behavior). A larger one — any meeting recording past ~13.6 minutes as a 16kHz
        mono WAV — would otherwise be rejected outright by the provider's upload size
        cap (see ``_CHUNK_TARGET_BYTES``'s comment), so it is split into several
        requests instead, each independently sent and merged back into one result with
        segment timestamps shifted to the original file's timeline."""
        if audio_path.stat().st_size <= self.chunk_target_bytes:
            result = self._request_transcription(audio_path, language=language)
            if on_segment is not None:
                for seg in result.segments:
                    on_segment(seg.end_s, result.duration_s)
            return result
        return self._transcribe_in_chunks(audio_path, language=language, on_segment=on_segment)

    def _transcribe_in_chunks(
        self, audio_path: Path, *, language: str | None, on_segment: OnSegment | None
    ) -> EngineResult:
        windows = _wav_chunk_windows(audio_path, max_bytes=self.chunk_target_bytes)
        if not windows:
            # Not a readable WAV (or empty) — let a single request's own error surface
            # normally rather than guessing why it can't be split.
            result = self._request_transcription(audio_path, language=language)
            if on_segment is not None:
                for seg in result.segments:
                    on_segment(seg.end_s, result.duration_s)
            return result

        total_duration_s = windows[-1][1]
        log.info(
            "splitting long recording for cloud upload",
            extra={"chunks": len(windows), "total_duration_s": total_duration_s},
        )
        all_segments: list[TranscriptSegment] = []
        language_result = language
        with tempfile.TemporaryDirectory(prefix="lmr-chunk-") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            for index, (start_s, end_s) in enumerate(windows):
                chunk_path = tmpdir / f"chunk_{index}.wav"
                _write_wav_window(audio_path, chunk_path, start_s=start_s, end_s=end_s)
                chunk_result = self._request_transcription(chunk_path, language=language)
                if language_result is None and chunk_result.language != "unknown":
                    language_result = chunk_result.language
                for seg in chunk_result.segments:
                    shifted = seg.model_copy(
                        update={"start_s": seg.start_s + start_s, "end_s": seg.end_s + start_s}
                    )
                    all_segments.append(shifted)
                    if on_segment is not None:
                        on_segment(shifted.end_s, total_duration_s)
        return EngineResult(
            language=language_result or "unknown",
            language_probability=None,
            duration_s=total_duration_s,
            segments=all_segments,
        )

    def _request_transcription(self, audio_path: Path, *, language: str | None) -> EngineResult:
        response_format = self._response_format()
        fields = {
            "model": self.model_name,
            "response_format": response_format,
            **(self.extra_transcribe_fields if response_format == "verbose_json" else {}),
        }
        if language:
            fields["language"] = language
        body, content_type = encode_multipart(fields, "file", audio_path)
        request = urllib.request.Request(
            self.transcribe_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
            },
        )
        payload = self._send(request)
        return self._parse_transcription_response(
            payload, language=language, fallback_duration_s=_local_wav_duration_s(audio_path)
        )

    @staticmethod
    def _parse_transcription_response(
        payload: dict[str, Any], *, language: str | None, fallback_duration_s: float
    ) -> EngineResult:
        raw_segments = payload.get("segments")
        if raw_segments:
            segments = [
                TranscriptSegment(
                    start_s=float(seg["start"]),
                    end_s=float(seg["end"]),
                    text=str(seg["text"]).strip(),
                )
                for seg in raw_segments
            ]
            duration = payload.get("duration")
            duration_s = float(duration) if duration is not None else fallback_duration_s
        else:
            # Plain `json` format (gpt-4o-transcribe/gpt-4o-mini-transcribe): only a
            # `text` field, no segments, no duration, no language. One segment spanning
            # the whole (locally measured) duration is the closest honest equivalent —
            # never invent per-sentence timestamps the provider did not give us.
            text = str(payload.get("text", "")).strip()
            duration_s = fallback_duration_s
            segments = [TranscriptSegment(start_s=0.0, end_s=duration_s, text=text)] if text else []
        return EngineResult(
            language=payload.get("language") or (language or "unknown"),
            language_probability=None,  # not reported by either provider's API
            duration_s=duration_s,
            segments=segments,
        )

    def test_connection(self, *, timeout: float = 10.0) -> ConnectionTestResult:
        """A cheap call that only validates the API key — never transcribes anything,
        never spends transcription minutes/credits."""
        request = urllib.request.Request(
            self.key_test_url, headers={"Authorization": f"Bearer {self._api_key}"}
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 300:
                    return ConnectionTestResult(ok=True, message="Connected successfully.")
                return ConnectionTestResult(ok=False, message=f"Unexpected response ({status}).")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return ConnectionTestResult(ok=False, message="Invalid or expired API key.")
            return ConnectionTestResult(ok=False, message=f"Request failed ({exc.code}).")
        except urllib.error.URLError as exc:
            return ConnectionTestResult(
                ok=False, message=f"Could not reach {self.provider_label}: {exc.reason}"
            )

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = sanitize_error_detail(exc.read().decode("utf-8", errors="replace"))
            raise TranscriptionError(
                f"{self.provider_label} transcription request failed ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TranscriptionError(
                f"{self.provider_label} transcription request failed: {exc.reason}"
            ) from exc
        try:
            result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TranscriptionError(
                f"{self.provider_label} returned a response that could not be parsed"
            ) from exc
        return result
