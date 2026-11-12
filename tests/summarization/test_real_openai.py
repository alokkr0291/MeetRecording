"""Real-provider acceptance test: an actual OpenAI chat-completions call, not the fake
HTTP opener used by ``tests/unit/summarization/test_providers.py``.

Gated by ``LMR_OPENAI_SUMMARY_TESTS=1`` *and* a real ``OPENAI_API_KEY`` in the
environment — this spends real API credit. Not part of the default unit/integration
suite. A skip here is not a pass; read the printed reason.
"""

from __future__ import annotations

import os

import pytest

from local_meeting_recorder.summarization.providers.openai_client import OpenAISummaryClient

OPENAI_SUMMARY_TESTS_ENV = "LMR_OPENAI_SUMMARY_TESTS"

pytestmark = pytest.mark.skipif(
    os.environ.get(OPENAI_SUMMARY_TESTS_ENV) != "1",
    reason=f"set {OPENAI_SUMMARY_TESTS_ENV}=1 to run against the real OpenAI API (spends credit)",
)

_TRANSCRIPT_TEXT = (
    "[0] System: Let's ship the beta on Friday.\n[1] You: I'll write the launch doc by Thursday."
)


def test_real_openai_generates_a_structured_summary() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("set OPENAI_API_KEY to run this test")

    client = OpenAISummaryClient(api_key=api_key)
    summary = client.generate_summary(_TRANSCRIPT_TEXT)

    assert summary.provider == "openai"
    assert summary.title.strip()
    assert summary.executive_summary.strip()
