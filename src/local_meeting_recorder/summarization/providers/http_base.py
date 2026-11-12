"""Shared HTTP plumbing for the OpenAI/OpenRouter summary clients.

Both expose an OpenAI-compatible ``/v1/chat/completions`` endpoint that accepts
Structured Outputs via ``response_format: {"type": "json_schema", ...}``, so a strict
JSON object matching ``SUMMARY_JSON_SCHEMA`` comes back directly — no free-form text to
parse. Each subclass sets the URLs and default model via class attributes.

``opener`` is injectable (defaults to ``urllib.request.urlopen``) so tests can supply a
fake HTTP transport — no real network calls, no real API keys, in the automated suite.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from local_meeting_recorder.domain.errors import SummaryError, sanitize_error_detail
from local_meeting_recorder.summarization.models import MeetingSummary
from local_meeting_recorder.summarization.protocol import SummaryConnectionTestResult
from local_meeting_recorder.summarization.schema import (
    SUMMARY_JSON_SCHEMA,
    SUMMARY_SYSTEM_PROMPT,
    build_transcript_user_message,
    parse_summary_content,
)

UrlOpener = Callable[..., Any]


class HttpSummaryClientBase:
    """Not used directly — ``OpenAISummaryClient``/``OpenRouterSummaryClient`` set the
    class attributes below and inherit this."""

    provider_label = "cloud provider"
    provider_id = ""
    chat_url = ""
    key_test_url = ""
    default_model = ""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        timeout: float = 300.0,
        opener: UrlOpener = urllib.request.urlopen,
    ) -> None:
        self.model_name = model or self.default_model
        self._api_key = api_key
        self._timeout = timeout
        self._opener = opener

    def generate_summary(self, transcript_text: str) -> MeetingSummary:
        body = json.dumps(
            {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": build_transcript_user_message(transcript_text)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "meeting_summary",
                        "schema": SUMMARY_JSON_SCHEMA,
                        "strict": True,
                    },
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.chat_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        payload = self._send(request)
        content = payload["choices"][0]["message"]["content"]
        return parse_summary_content(content, provider=self.provider_id, model_name=self.model_name)

    def test_connection(self, *, timeout: float = 10.0) -> SummaryConnectionTestResult:
        """A cheap call that only validates the API key — never generates a summary."""
        request = urllib.request.Request(
            self.key_test_url, headers={"Authorization": f"Bearer {self._api_key}"}
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 300:
                    return SummaryConnectionTestResult(ok=True, message="Connected successfully.")
                return SummaryConnectionTestResult(
                    ok=False, message=f"Unexpected response ({status})."
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return SummaryConnectionTestResult(ok=False, message="Invalid or expired API key.")
            return SummaryConnectionTestResult(ok=False, message=f"Request failed ({exc.code}).")
        except urllib.error.URLError as exc:
            return SummaryConnectionTestResult(
                ok=False, message=f"Could not reach {self.provider_label}: {exc.reason}"
            )

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = sanitize_error_detail(exc.read().decode("utf-8", errors="replace"))
            raise SummaryError(
                f"{self.provider_label} summary request failed ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SummaryError(
                f"{self.provider_label} summary request failed: {exc.reason}"
            ) from exc
        try:
            result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SummaryError(
                f"{self.provider_label} returned a response that could not be parsed"
            ) from exc
        return result
