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


def _local_wav_duration_s(audio_path: Path) -> float:
    """Best-effort duration of the local (always-WAV) file, used as a fallback when the
    provider's response does not report one (the plain ``json`` format never does)."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            rate = wav_file.getframerate()
            return wav_file.getnframes() / rate if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


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
    ) -> None:
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
        result = self._parse_transcription_response(
            payload, language=language, fallback_duration_s=_local_wav_duration_s(audio_path)
        )
        if on_segment is not None:
            for seg in result.segments:
                on_segment(seg.end_s, result.duration_s)
        return result

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
