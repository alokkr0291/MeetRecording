"""OpenRouter summary client: Structured Outputs on OpenRouter's OpenAI-compatible
``/v1/chat/completions`` endpoint (https://openrouter.ai/docs/features/structured-outputs).

Not every model OpenRouter proxies to supports ``json_schema`` strict mode — OpenRouter
returns an ordinary error response for one that doesn't, which surfaces as a
``SummaryError`` like any other failed request. There is no free-model default here (see
``openrouter_summary_model`` in ``settings/models.py``): the user must pick one that is
known to support structured output.
"""

from __future__ import annotations

from local_meeting_recorder.summarization.providers.http_base import HttpSummaryClientBase


class OpenRouterSummaryClient(HttpSummaryClientBase):
    provider_label = "OpenRouter"
    provider_id = "openrouter"
    chat_url = "https://openrouter.ai/api/v1/chat/completions"
    key_test_url = "https://openrouter.ai/api/v1/models"
    default_model = ""
