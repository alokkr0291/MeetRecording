"""OpenAI audio transcription: ``POST /v1/audio/transcriptions`` (multipart), key
validated cheaply via ``GET /v1/models`` (no transcription spend).

Model choice is a real tradeoff, not just price: only ``whisper-1`` supports
``response_format=verbose_json`` (per-segment timestamps); ``gpt-4o-transcribe`` and
``gpt-4o-mini-transcribe`` only return a single ``text`` blob (see
``http_base.py``'s ``segment_capable_models``/``_parse_transcription_response``).
Pricing below is per OpenAI's published rates, verified via web search 2026-11-10 (not
guessed) — confirm against OpenAI's pricing page before relying on it, prices change.
OpenAI has also announced it will retire all of these transcription models (and
``/v1/audio/translations``) on 2027-02-26.
"""

from __future__ import annotations

from typing import ClassVar

from local_meeting_recorder.transcription.providers.http_base import HttpTranscriptionClientBase

DEFAULT_MODEL = "whisper-1"

# (model id, UI label) — cheapest first is *not* assumed; whisper-1 stays the default
# because it is the only one with segment timestamps, which the rest of this app's UI
# (the transcript viewer, timestamp validation) is built around. The other two are
# cheaper/faster but text-only — a real, informed tradeoff, surfaced in the label.
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("whisper-1", "whisper-1 — $0.006/min, segment timestamps"),
    ("gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe — $0.003/min, cheapest, text only"),
    ("gpt-4o-transcribe", "gpt-4o-transcribe — $0.006/min, most accurate, text only"),
]


class OpenAITranscriptionClient(HttpTranscriptionClientBase):
    provider_label = "OpenAI"
    transcribe_url = "https://api.openai.com/v1/audio/transcriptions"
    key_test_url = "https://api.openai.com/v1/models"
    default_model = DEFAULT_MODEL
    extra_transcribe_fields: ClassVar[dict[str, str]] = {}
    segment_capable_models: ClassVar[set[str] | None] = {"whisper-1"}
