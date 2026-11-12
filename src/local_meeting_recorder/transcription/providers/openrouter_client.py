"""OpenRouter audio transcription: ``POST /api/v1/audio/transcriptions`` (OpenAI-
compatible multipart), key validated via its purpose-built ``GET /api/v1/key`` endpoint.

``timestamp_granularities`` is sent as a single ``"segment"`` form field; OpenRouter's
docs show it as a JSON array in a JSON request body, and the exact multipart form
encoding for an array value was not verified against a live account (no API key
available in this environment) — this is a best-effort form-encoding of the same
intent, worth confirming against a real key before relying on it. See
IMPLEMENTATION_STATUS.md.
"""

from __future__ import annotations

from typing import ClassVar

from local_meeting_recorder.transcription.providers.http_base import HttpTranscriptionClientBase

DEFAULT_MODEL = "openai/whisper-large-v3"


class OpenRouterTranscriptionClient(HttpTranscriptionClientBase):
    provider_label = "OpenRouter"
    transcribe_url = "https://openrouter.ai/api/v1/audio/transcriptions"
    key_test_url = "https://openrouter.ai/api/v1/key"
    default_model = DEFAULT_MODEL
    extra_transcribe_fields: ClassVar[dict[str, str]] = {"timestamp_granularities": "segment"}
