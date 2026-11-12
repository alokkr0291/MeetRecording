"""Builds the ``TranscriptionProviderClient`` for the currently selected provider.

Called fresh by ``TranscriptionManager``'s ``engine_factory`` closure each time it needs
one (see ``app.py``) — reads live settings/credentials, never caches here itself.
"""

from __future__ import annotations

from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import (
    MISSING_API_KEY_CODE,
    CredentialStoreError,
    TranscriptionError,
)
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.settings.models import (
    AppSettings,
    ProviderAvailability,
    TranscriptionProviderId,
)
from local_meeting_recorder.settings.profiles import effects_for
from local_meeting_recorder.transcription.engine import TranscriptionEngine
from local_meeting_recorder.transcription.providers.openai_client import OpenAITranscriptionClient
from local_meeting_recorder.transcription.providers.openrouter_client import (
    OpenRouterTranscriptionClient,
)
from local_meeting_recorder.transcription.providers.protocol import TranscriptionProviderClient

OPENAI_API_KEY_CREDENTIAL = "openai_api_key"
OPENROUTER_API_KEY_CREDENTIAL = "openrouter_api_key"

CREDENTIAL_KEY_BY_PROVIDER = {
    TranscriptionProviderId.OPENAI: OPENAI_API_KEY_CREDENTIAL,
    TranscriptionProviderId.OPENROUTER: OPENROUTER_API_KEY_CREDENTIAL,
}

# Re-exported for backward compatibility — existing imports of
# ``transcription.providers.factory.MISSING_API_KEY_CODE`` keep working; the shared
# definition now lives in domain/errors.py so summarization/providers/factory.py (Phase
# 5E) can reuse the exact same constant without importing from this module.
__all__ = ["MISSING_API_KEY_CODE", "build_provider_client", "provider_availability"]


def build_provider_client(
    *,
    app_settings: AppSettings,
    dev_settings: Settings,
    credential_store: CredentialStore,
) -> TranscriptionProviderClient:
    """Raises ``TranscriptionError`` if a selected cloud provider has no API key
    configured — the caller (``TranscriptionManager``) turns that into an ordinary
    transcription failure for this one job, exactly like any other transcription error.
    It never blocks local recording, never blocks local faster-whisper, and never
    raises a fatal application error."""
    provider = app_settings.transcription_provider
    if provider is TranscriptionProviderId.LOCAL_WHISPER:
        return TranscriptionEngine(
            model_size=app_settings.whisper_model,
            device=dev_settings.whisper_device,
            compute_type=dev_settings.whisper_compute_type,
            cpu_threads=effects_for(app_settings.processing_profile).cpu_threads,
        )
    if provider is TranscriptionProviderId.OPENAI:
        api_key = credential_store.get(OPENAI_API_KEY_CREDENTIAL)
        if not api_key:
            raise TranscriptionError(
                "OpenAI is selected but no API key is configured. Open Settings to add one.",
                code=MISSING_API_KEY_CODE,
            )
        return OpenAITranscriptionClient(api_key=api_key, model=app_settings.openai_model)
    if provider is TranscriptionProviderId.OPENROUTER:
        api_key = credential_store.get(OPENROUTER_API_KEY_CREDENTIAL)
        if not api_key:
            raise TranscriptionError(
                "OpenRouter is selected but no API key is configured. Open Settings to add one.",
                code=MISSING_API_KEY_CODE,
            )
        return OpenRouterTranscriptionClient(api_key=api_key, model=app_settings.openrouter_model)
    raise TranscriptionError(f"unknown transcription provider: {provider!r}")


def provider_availability(
    provider: TranscriptionProviderId, credential_store: CredentialStore
) -> ProviderAvailability:
    """What the Settings UI should show for ``provider`` right now. Both cloud clients
    are fully implemented (see ``openai_client.py``/``openrouter_client.py``), so the
    only thing that can be missing is a credential — ``COMING_SOON`` exists for a
    provider this app has not actually wired up yet, which today is none of them."""
    if provider is TranscriptionProviderId.LOCAL_WHISPER:
        return ProviderAvailability.READY
    key = CREDENTIAL_KEY_BY_PROVIDER[provider]
    try:
        has_key = bool(credential_store.get(key))
    except CredentialStoreError:
        has_key = False
    return ProviderAvailability.READY if has_key else ProviderAvailability.NEEDS_API_KEY
