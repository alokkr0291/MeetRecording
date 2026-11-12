"""Real-provider acceptance test: an actual OpenRouter chat-completions call, not the
fake HTTP opener used by ``tests/unit/summarization/test_providers.py``.

Gated by ``LMR_OPENROUTER_SUMMARY_TESTS=1``, a real ``OPENROUTER_API_KEY``, and
``LMR_OPENROUTER_TEST_MODEL`` (a real OpenRouter model id — there is no safe default to
assume on the caller's behalf) — this spends real API credit. Not part of the default
unit/integration suite. A skip here is not a pass; read the printed reason.
"""

from __future__ import annotations

import os

import pytest

from local_meeting_recorder.summarization.providers.openrouter_client import (
    OpenRouterSummaryClient,
)

OPENROUTER_SUMMARY_TESTS_ENV = "LMR_OPENROUTER_SUMMARY_TESTS"
OPENROUTER_TEST_MODEL_ENV = "LMR_OPENROUTER_TEST_MODEL"

pytestmark = pytest.mark.skipif(
    os.environ.get(OPENROUTER_SUMMARY_TESTS_ENV) != "1",
    reason=(
        f"set {OPENROUTER_SUMMARY_TESTS_ENV}=1 to run against the real OpenRouter API "
        "(spends credit)"
    ),
)

_TRANSCRIPT_TEXT = (
    "[0] System: Let's ship the beta on Friday.\n[1] You: I'll write the launch doc by Thursday."
)


def test_real_openrouter_generates_a_structured_summary() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get(OPENROUTER_TEST_MODEL_ENV)
    if not api_key:
        pytest.skip("set OPENROUTER_API_KEY to run this test")
    if not model:
        pytest.skip(f"set {OPENROUTER_TEST_MODEL_ENV} to a real OpenRouter model id")

    client = OpenRouterSummaryClient(api_key=api_key, model=model)
    summary = client.generate_summary(_TRANSCRIPT_TEXT)

    assert summary.provider == "openrouter"
    assert summary.model_name == model
    assert summary.title.strip()
    assert summary.executive_summary.strip()
