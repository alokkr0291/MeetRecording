"""Cloud transcription provider clients (OpenAI, OpenRouter) against a fake HTTP
transport — no real network calls, no real API keys, matching the project's "fake the
external boundary" convention (``capture/fake.py``, ``test_engine.py``'s fake
``WhisperModel``).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import TranscriptionError
from local_meeting_recorder.infrastructure.credentials.memory import InMemoryCredentialStore
from local_meeting_recorder.settings.models import (
    AppSettings,
    ProviderAvailability,
    TranscriptionProviderId,
)
from local_meeting_recorder.transcription.engine import TranscriptionEngine
from local_meeting_recorder.transcription.providers.factory import (
    MISSING_API_KEY_CODE,
    OPENAI_API_KEY_CREDENTIAL,
    OPENROUTER_API_KEY_CREDENTIAL,
    build_provider_client,
    provider_availability,
)
from local_meeting_recorder.transcription.providers.openai_client import OpenAITranscriptionClient
from local_meeting_recorder.transcription.providers.openrouter_client import (
    OpenRouterTranscriptionClient,
)


@dataclass
class _FakeResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@dataclass
class _FakeOpener:
    response: _FakeResponse | None = None
    error: Exception | None = None
    requests: list[urllib.request.Request] = field(default_factory=list)

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _verbose_json_body(*, language: str = "en", duration: float = 12.5) -> bytes:
    payload = {
        "language": language,
        "duration": duration,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " hello "},
            {"start": 1.5, "end": 3.0, "text": "world"},
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _http_error(*, code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.invalid", code=code, msg="error", hdrs=None, fp=io.BytesIO(body)
    )


def _audio_stub(tmp_path: Path) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


def _real_wav(tmp_path: Path, *, frames: int = 1600, rate: int = 16_000) -> Path:
    """A real, readable WAV file (unlike ``_audio_stub``) so ``_local_wav_duration_s``
    can measure it — used by the plain-``json``-format fallback tests."""
    path = tmp_path / "real_audio.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * frames)
    return path


# -- OpenAI -----------------------------------------------------------------------------


def test_openai_transcribe_parses_segments_and_metadata(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=_verbose_json_body()))
    client = OpenAITranscriptionClient(api_key="sk-test", opener=opener)
    audio_path = _audio_stub(tmp_path)

    seen: list[tuple[float, float | None]] = []
    result = client.transcribe(audio_path, on_segment=lambda p, t: seen.append((p, t)))

    assert result.language == "en"
    assert result.duration_s == 12.5
    assert [(s.start_s, s.end_s, s.text) for s in result.segments] == [
        (0.0, 1.5, "hello"),
        (1.5, 3.0, "world"),
    ]
    assert seen == [(1.5, 12.5), (3.0, 12.5)]


def test_openai_transcribe_sends_bearer_auth_and_model(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=_verbose_json_body()))
    client = OpenAITranscriptionClient(api_key="sk-test", model="whisper-1", opener=opener)
    audio_path = _audio_stub(tmp_path)

    client.transcribe(audio_path)
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer sk-test"
    body = request.data
    assert b'name="model"' in body
    assert b"whisper-1" in body
    assert b'name="response_format"' in body
    assert b"verbose_json" in body


def test_openai_transcribe_http_error_is_wrapped(tmp_path: Path) -> None:
    opener = _FakeOpener(error=_http_error(code=400, body=b"bad request"))
    client = OpenAITranscriptionClient(api_key="sk-test", opener=opener)
    audio_path = _audio_stub(tmp_path)

    with pytest.raises(TranscriptionError):
        client.transcribe(audio_path)


def test_openai_gpt4o_mini_requests_plain_json_not_verbose(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=b'{"text": "hello world"}'))
    client = OpenAITranscriptionClient(
        api_key="sk-test", model="gpt-4o-mini-transcribe", opener=opener
    )
    audio_path = _audio_stub(tmp_path)

    client.transcribe(audio_path)
    body = opener.requests[0].data
    assert b'name="response_format"' in body
    assert b"verbose_json" not in body


def test_openai_gpt4o_mini_falls_back_to_one_segment_spanning_local_duration(
    tmp_path: Path,
) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=b'{"text": "hello world"}'))
    client = OpenAITranscriptionClient(
        api_key="sk-test", model="gpt-4o-mini-transcribe", opener=opener
    )
    audio_path = _real_wav(tmp_path, frames=1600, rate=16_000)  # 0.1s

    result = client.transcribe(audio_path)

    assert len(result.segments) == 1
    assert result.segments[0].text == "hello world"
    assert result.segments[0].start_s == 0.0
    assert result.segments[0].end_s == pytest.approx(0.1)
    assert result.duration_s == pytest.approx(0.1)
    assert result.language == "unknown"


def test_openai_gpt4o_mini_with_empty_text_produces_no_segments(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=b'{"text": ""}'))
    client = OpenAITranscriptionClient(
        api_key="sk-test", model="gpt-4o-mini-transcribe", opener=opener
    )
    audio_path = _real_wav(tmp_path)

    result = client.transcribe(audio_path)
    assert result.segments == []


def test_openai_whisper1_still_requests_verbose_json(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=_verbose_json_body()))
    client = OpenAITranscriptionClient(api_key="sk-test", model="whisper-1", opener=opener)
    audio_path = _audio_stub(tmp_path)

    client.transcribe(audio_path)
    assert b"verbose_json" in opener.requests[0].data


def test_openai_test_connection_ok() -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=b'{"data": []}'))
    client = OpenAITranscriptionClient(api_key="sk-test", opener=opener)
    result = client.test_connection()
    assert result.ok
    assert opener.requests[0].full_url == "https://api.openai.com/v1/models"


def test_openai_test_connection_invalid_key() -> None:
    opener = _FakeOpener(error=_http_error(code=401))
    client = OpenAITranscriptionClient(api_key="sk-bad", opener=opener)
    result = client.test_connection()
    assert not result.ok
    assert "Invalid" in result.message


def test_openai_test_connection_network_error() -> None:
    opener = _FakeOpener(error=urllib.error.URLError("no route to host"))
    client = OpenAITranscriptionClient(api_key="sk-test", opener=opener)
    result = client.test_connection()
    assert not result.ok
    assert "OpenAI" in result.message


# -- OpenRouter --------------------------------------------------------------------------


def test_openrouter_transcribe_uses_its_own_endpoint_and_extra_field(tmp_path: Path) -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=_verbose_json_body()))
    client = OpenRouterTranscriptionClient(api_key="or-test", opener=opener)
    audio_path = _audio_stub(tmp_path)

    client.transcribe(audio_path)
    request = opener.requests[0]
    assert request.full_url == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert b'name="timestamp_granularities"' in request.data


def test_openrouter_test_connection_uses_key_endpoint() -> None:
    opener = _FakeOpener(response=_FakeResponse(status=200, body=b"{}"))
    client = OpenRouterTranscriptionClient(api_key="or-test", opener=opener)
    result = client.test_connection()
    assert result.ok
    assert opener.requests[0].full_url == "https://openrouter.ai/api/v1/key"


# -- factory ------------------------------------------------------------------------------


def test_factory_builds_local_whisper_engine() -> None:
    client = build_provider_client(
        app_settings=AppSettings(transcription_provider=TranscriptionProviderId.LOCAL_WHISPER),
        dev_settings=Settings(_env_file=None),  # type: ignore[call-arg]
        credential_store=InMemoryCredentialStore(),
    )
    assert isinstance(client, TranscriptionEngine)


def test_factory_raises_for_openai_without_api_key() -> None:
    with pytest.raises(TranscriptionError, match="no API key") as excinfo:
        build_provider_client(
            app_settings=AppSettings(transcription_provider=TranscriptionProviderId.OPENAI),
            dev_settings=Settings(_env_file=None),  # type: ignore[call-arg]
            credential_store=InMemoryCredentialStore(),
        )
    assert excinfo.value.code == MISSING_API_KEY_CODE


def test_factory_builds_openai_client_when_key_present() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set(OPENAI_API_KEY_CREDENTIAL, "sk-test")
    client = build_provider_client(
        app_settings=AppSettings(transcription_provider=TranscriptionProviderId.OPENAI),
        dev_settings=Settings(_env_file=None),  # type: ignore[call-arg]
        credential_store=credentials,
    )
    assert isinstance(client, OpenAITranscriptionClient)


def test_factory_raises_for_openrouter_without_api_key() -> None:
    with pytest.raises(TranscriptionError, match="no API key") as excinfo:
        build_provider_client(
            app_settings=AppSettings(transcription_provider=TranscriptionProviderId.OPENROUTER),
            dev_settings=Settings(_env_file=None),  # type: ignore[call-arg]
            credential_store=InMemoryCredentialStore(),
        )
    assert excinfo.value.code == MISSING_API_KEY_CODE


def test_factory_builds_openrouter_client_when_key_present() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set(OPENROUTER_API_KEY_CREDENTIAL, "or-test")
    client = build_provider_client(
        app_settings=AppSettings(transcription_provider=TranscriptionProviderId.OPENROUTER),
        dev_settings=Settings(_env_file=None),  # type: ignore[call-arg]
        credential_store=credentials,
    )
    assert isinstance(client, OpenRouterTranscriptionClient)


# -- provider_availability ----------------------------------------------------------------


def test_local_whisper_is_always_ready() -> None:
    assert (
        provider_availability(TranscriptionProviderId.LOCAL_WHISPER, InMemoryCredentialStore())
        is ProviderAvailability.READY
    )


def test_openai_needs_api_key_when_none_configured() -> None:
    assert (
        provider_availability(TranscriptionProviderId.OPENAI, InMemoryCredentialStore())
        is ProviderAvailability.NEEDS_API_KEY
    )


def test_openai_is_ready_once_a_key_is_configured() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set(OPENAI_API_KEY_CREDENTIAL, "sk-test")
    assert (
        provider_availability(TranscriptionProviderId.OPENAI, credentials)
        is ProviderAvailability.READY
    )


def test_openrouter_availability_tracks_its_own_key() -> None:
    credentials = InMemoryCredentialStore()
    assert (
        provider_availability(TranscriptionProviderId.OPENROUTER, credentials)
        is ProviderAvailability.NEEDS_API_KEY
    )
    credentials.set(OPENROUTER_API_KEY_CREDENTIAL, "or-test")
    assert (
        provider_availability(TranscriptionProviderId.OPENROUTER, credentials)
        is ProviderAvailability.READY
    )
