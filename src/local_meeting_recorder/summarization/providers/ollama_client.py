"""Ollama summary client: local, no API key, no cloud requirement by default.

Uses ``/api/chat`` with Ollama's structured-output ``format`` field (a JSON schema,
supported since Ollama 0.5 — https://ollama.com/blog/structured-outputs), so no prompt
hacks or regex-scraping of free-form text are needed to get a reliable shape back.
``opener`` is injectable so tests never make a real network call to a local Ollama
server.
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

PROVIDER_NAME = "ollama"


class OllamaSummaryClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 300.0,
        opener: UrlOpener = urllib.request.urlopen,
    ) -> None:
        self.model_name = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._opener = opener

    def generate_summary(self, transcript_text: str) -> MeetingSummary:
        request = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(
                {
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": build_transcript_user_message(transcript_text)},
                    ],
                    "format": SUMMARY_JSON_SCHEMA,
                    "stream": False,
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        payload = self._send(request)
        content = payload.get("message", {}).get("content", "")
        return parse_summary_content(content, provider=PROVIDER_NAME, model_name=self.model_name)

    def test_connection(self, *, timeout: float = 10.0) -> SummaryConnectionTestResult:
        """Checks both that the local Ollama server is reachable *and* that the
        configured model is actually installed locally — a reachable server with the
        wrong model name previously reported "Connected successfully" and only failed
        later, during a real summary job. Never generates a summary itself."""
        request = urllib.request.Request(f"{self._base_url}/api/tags")
        try:
            with self._opener(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if not (200 <= status < 300):
                    return SummaryConnectionTestResult(
                        ok=False, message=f"Unexpected response ({status})."
                    )
                raw = response.read()
        except urllib.error.HTTPError as exc:
            return SummaryConnectionTestResult(ok=False, message=f"Request failed ({exc.code}).")
        except urllib.error.URLError as exc:
            return SummaryConnectionTestResult(
                ok=False, message=f"Could not reach Ollama at {self._base_url}: {exc.reason}"
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return SummaryConnectionTestResult(
                ok=True,
                message="Connected, but could not read the installed model list to verify "
                f"'{self.model_name}' is available.",
            )

        available = _installed_model_names(payload)
        if _model_is_available(self.model_name, available):
            return SummaryConnectionTestResult(
                ok=True, message=f"Connected. Model '{self.model_name}' is available."
            )
        return SummaryConnectionTestResult(
            ok=False,
            message=(
                f"Connected to Ollama, but model '{self.model_name}' is not installed "
                f"locally. Run `ollama pull {self.model_name}` and try again."
            ),
        )

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = sanitize_error_detail(exc.read().decode("utf-8", errors="replace"))
            raise SummaryError(f"Ollama summary request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise SummaryError(f"Could not reach Ollama: {exc.reason}") from exc
        try:
            result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SummaryError("Ollama returned a response that could not be parsed") from exc
        return result


def _installed_model_names(tags_payload: dict[str, Any]) -> set[str]:
    """Every name Ollama's ``/api/tags`` response identifies an installed model by —
    both the ``name`` and ``model`` fields, defensively, since real responses have been
    observed to vary in which one they populate."""
    names: set[str] = set()
    models = tags_payload.get("models")
    if not isinstance(models, list):
        return names
    for entry in models:
        if not isinstance(entry, dict):
            continue
        for field_name in ("name", "model"):
            value = entry.get(field_name)
            if isinstance(value, str) and value:
                names.add(value)
    return names


def _model_is_available(model: str, available: set[str]) -> bool:
    """Tolerant match: Ollama lists installed models with an explicit tag
    (``"llama3.1:latest"``), but a user configuring the model name in Settings often
    omits it (``"llama3.1"``) — compare both the exact name and the name with any
    ``:tag`` suffix stripped from either side."""
    if model in available:
        return True
    bare_model = model.split(":", 1)[0]
    return any(name == model or name.split(":", 1)[0] == bare_model for name in available)
