"""OpenAI summary client: Structured Outputs on ``/v1/chat/completions``.

https://platform.openai.com/docs/guides/structured-outputs — supported by every current
chat model, including ``gpt-4o-mini`` (the default: cheapest capable model as of this
writing, same reasoning as the transcription provider's model picker).
"""

from __future__ import annotations

from local_meeting_recorder.summarization.providers.http_base import HttpSummaryClientBase

AVAILABLE_MODELS = [
    ("gpt-4o-mini", "GPT-4o mini (cheapest, recommended)"),
    ("gpt-4o", "GPT-4o"),
]


class OpenAISummaryClient(HttpSummaryClientBase):
    provider_label = "OpenAI"
    provider_id = "openai"
    chat_url = "https://api.openai.com/v1/chat/completions"
    key_test_url = "https://api.openai.com/v1/models"
    default_model = "gpt-4o-mini"
