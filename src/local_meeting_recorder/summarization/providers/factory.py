"""Builds the ``SummaryProviderClient`` for the currently selected summary provider.

Called fresh by ``SummaryManager``'s ``client_factory`` closure each time it needs one
(see ``app.py``) — reads live settings/credentials, never caches here itself. OpenAI and
OpenRouter summaries reuse the exact same API key credential as the transcription
provider of the same name (``CREDENTIAL_KEY_BY_PROVIDER`` in
``transcription/providers/factory.py``) — it is the same account either way, so there is
no reason to ask the user to enter the same key twice.
"""

from __future__ import annotations

from local_meeting_recorder.domain.errors import (
    MISSING_API_KEY_CODE,
    CredentialStoreError,
    SummaryError,
)
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.settings.models import (
    AppSettings,
    ProviderAvailability,
    SummaryProviderId,
)
from local_meeting_recorder.summarization.protocol import SummaryProviderClient
from local_meeting_recorder.summarization.providers.ollama_client import OllamaSummaryClient
from local_meeting_recorder.summarization.providers.openai_client import OpenAISummaryClient
from local_meeting_recorder.summarization.providers.openrouter_client import (
    OpenRouterSummaryClient,
)
from local_meeting_recorder.transcription.providers.factory import (
    OPENAI_API_KEY_CREDENTIAL,
    OPENROUTER_API_KEY_CREDENTIAL,
)

CREDENTIAL_KEY_BY_SUMMARY_PROVIDER = {
    SummaryProviderId.OPENAI: OPENAI_API_KEY_CREDENTIAL,
    SummaryProviderId.OPENROUTER: OPENROUTER_API_KEY_CREDENTIAL,
}


def build_summary_client(
    *, app_settings: AppSettings, credential_store: CredentialStore
) -> SummaryProviderClient:
    """Raises ``SummaryError`` if a selected cloud provider has no API key configured, or
    if Ollama has no model name configured — the caller (``SummaryManager``) turns that
    into an ordinary summary failure for this one job, never a fatal application error."""
    provider = app_settings.summary_provider
    if provider is SummaryProviderId.OLLAMA:
        if not app_settings.ollama_summary_model:
            raise SummaryError(
                "No Ollama model is configured for summaries. Open Settings to choose one."
            )
        return OllamaSummaryClient(
            base_url=app_settings.ollama_base_url, model=app_settings.ollama_summary_model
        )
    if provider is SummaryProviderId.OPENAI:
        api_key = credential_store.get(OPENAI_API_KEY_CREDENTIAL)
        if not api_key:
            raise SummaryError(
                "OpenAI is selected for summaries but no API key is configured. Open "
                "Settings to add one.",
                code=MISSING_API_KEY_CODE,
            )
        return OpenAISummaryClient(api_key=api_key, model=app_settings.openai_summary_model)
    if provider is SummaryProviderId.OPENROUTER:
        api_key = credential_store.get(OPENROUTER_API_KEY_CREDENTIAL)
        if not api_key:
            raise SummaryError(
                "OpenRouter is selected for summaries but no API key is configured. Open "
                "Settings to add one.",
                code=MISSING_API_KEY_CODE,
            )
        if not app_settings.openrouter_summary_model:
            raise SummaryError(
                "No OpenRouter model is configured for summaries. Open Settings to choose one."
            )
        return OpenRouterSummaryClient(api_key=api_key, model=app_settings.openrouter_summary_model)
    raise SummaryError(f"unknown summary provider: {provider!r}")


def summary_provider_availability(
    provider: SummaryProviderId, credential_store: CredentialStore
) -> ProviderAvailability:
    """What the Settings UI should show for ``provider`` right now."""
    if provider is SummaryProviderId.OLLAMA:
        return ProviderAvailability.READY
    key = CREDENTIAL_KEY_BY_SUMMARY_PROVIDER[provider]
    try:
        has_key = bool(credential_store.get(key))
    except CredentialStoreError:
        has_key = False
    return ProviderAvailability.READY if has_key else ProviderAvailability.NEEDS_API_KEY
