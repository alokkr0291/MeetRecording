"""Real-provider acceptance test: an actual local Ollama server, not the fake HTTP
opener used by ``tests/unit/summarization/test_providers.py``.

Gated by ``LMR_OLLAMA_TESTS=1`` *and* ``LMR_OLLAMA_TEST_MODEL`` (a model name already
pulled on that server — there is no universal default to assume, since it depends on
what the machine running this test has installed). Not part of the default
unit/integration suite. A skip here is not a pass; read the printed reason.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from local_meeting_recorder.summarization.providers.ollama_client import OllamaSummaryClient

OLLAMA_TESTS_ENV = "LMR_OLLAMA_TESTS"
OLLAMA_TEST_MODEL_ENV = "LMR_OLLAMA_TEST_MODEL"
OLLAMA_BASE_URL_ENV = "LMR_OLLAMA_TEST_BASE_URL"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

pytestmark = pytest.mark.skipif(
    os.environ.get(OLLAMA_TESTS_ENV) != "1",
    reason=f"set {OLLAMA_TESTS_ENV}=1 to run against a real local Ollama server",
)

_TRANSCRIPT_TEXT = (
    "[0] System: Let's ship the beta on Friday.\n[1] You: I'll write the launch doc by Thursday."
)


def test_real_ollama_generates_a_structured_summary() -> None:
    model = os.environ.get(OLLAMA_TEST_MODEL_ENV)
    if not model:
        pytest.skip(f"set {OLLAMA_TEST_MODEL_ENV} to a model already pulled on your Ollama server")
    base_url = os.environ.get(OLLAMA_BASE_URL_ENV, DEFAULT_BASE_URL)

    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5):
            pass
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"could not reach Ollama at {base_url}: {exc}")

    client = OllamaSummaryClient(base_url=base_url, model=model)
    summary = client.generate_summary(_TRANSCRIPT_TEXT)

    assert summary.provider == "ollama"
    assert summary.model_name == model
    assert summary.title.strip()
    assert summary.executive_summary.strip()
