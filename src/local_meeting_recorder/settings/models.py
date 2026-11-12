"""``AppSettings``: the Settings-window-managed preferences schema."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

APP_SETTINGS_SCHEMA_VERSION = 1


class TranscriptionProviderId(StrEnum):
    LOCAL_WHISPER = "local_whisper"
    OPENAI = "openai"
    OPENROUTER = "openrouter"


class SummaryProviderId(StrEnum):
    """Phase 5E: completely independent of ``TranscriptionProviderId`` — changing the
    summary model never changes the Whisper model or diarization model."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    OPENROUTER = "openrouter"


class ProviderAvailability(StrEnum):
    """What the Settings UI shows for a provider — never pretend one works when it
    does not: ``NEEDS_API_KEY`` and ``READY`` both mean "implemented", just whether a
    credential is configured yet; ``COMING_SOON`` means the client genuinely isn't
    wired up (none currently are — see ``transcription/providers/``)."""

    READY = "ready"
    NEEDS_API_KEY = "needs_api_key"
    COMING_SOON = "coming_soon"


class ProcessingProfile(StrEnum):
    """Phase 6: one knob that trades AI quality/concurrency for resource usage on an
    entry-level CPU, instead of exposing every individual setting it affects. See
    ``settings/profiles.py`` for exactly what each profile changes."""

    LOW_RESOURCE = "low_resource"
    BALANCED = "balanced"
    QUALITY = "quality"


class AppSettings(BaseModel):
    """Non-secret preferences only — no API keys live here (see
    ``infrastructure/credentials/`` for those). Every field has a default so a missing
    or freshly-created settings file is always valid."""

    schema_version: int = APP_SETTINGS_SCHEMA_VERSION

    # General ---------------------------------------------------------------------------
    recordings_dir: Path | None = Field(
        default=None,
        description="Where new recording session folders are created. None = the "
        "default Application Support location.",
    )

    # Processing profile (Phase 6) --------------------------------------------------------
    processing_profile: ProcessingProfile = ProcessingProfile.BALANCED
    """Changing this applies its recommended Whisper model immediately (and, for Low
    Resource, turns diarization off) — see ``settings/profiles.py``. The user can still
    override the Whisper model afterward; the profile sets a sensible starting point,
    it doesn't lock the choice."""

    # Transcription -----------------------------------------------------------------------
    transcription_provider: TranscriptionProviderId = TranscriptionProviderId.LOCAL_WHISPER
    whisper_model: str = "base"
    openai_model: str = "whisper-1"
    openrouter_model: str = "openai/whisper-large-v3"

    # Speaker diarization (Phase 5C) -----------------------------------------------------
    diarization_enabled: bool = False
    """Off by default: the model is a multi-hundred-MB download requiring a Hugging
    Face token the user must supply — opt-in, not a surprise on first transcription."""
    diarization_model_download_confirmed: bool = False
    """Set once the user has clicked through the "this downloads a large model" dialog
    (Settings' Speakers tab) — lets a later re-enable skip the confirmation, while a
    first-ever enable always asks first."""

    # AI meeting summaries (Phase 5E) — field names are deliberately distinct from the
    # transcription-purpose `openai_model`/`openrouter_model` above so the two are
    # structurally incapable of colliding, not just conventionally kept apart ----------
    summary_enabled: bool = False
    """Off by default: a summary needs either a local Ollama server running or a cloud
    API key — never assumed, same opt-in reasoning as ``diarization_enabled``."""
    summary_provider: SummaryProviderId = SummaryProviderId.OLLAMA
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_summary_model: str = ""
    openai_summary_model: str = "gpt-4o-mini"
    openrouter_summary_model: str = ""
