"""Derives concrete, CPU-first processing behavior from the user-chosen
``ProcessingProfile`` (Phase 6) — a single lookup table instead of scattering
``if profile is ...`` branches across the transcription/diarization/summary managers.

Every profile is CPU-only; none of this ever selects or requires a GPU. "Quality" means
a larger Whisper model and allowing diarization/cloud AI, not a different device.
"""

from __future__ import annotations

from dataclasses import dataclass

from local_meeting_recorder.settings.models import ProcessingProfile

#: Applied to AppSettings.whisper_model immediately when the user picks a profile in
#: Settings — a starting point, not a lock; the user can still change the model
#: afterward via the existing Whisper model dropdown.
RECOMMENDED_WHISPER_MODEL: dict[ProcessingProfile, str] = {
    ProcessingProfile.LOW_RESOURCE: "tiny",
    ProcessingProfile.BALANCED: "base",
    ProcessingProfile.QUALITY: "small",
}


@dataclass(frozen=True)
class ProfileEffects:
    max_concurrent_ai_jobs: int
    """Passed to ``ResourceGovernor.set_limit()``. ``0`` = unlimited (today's
    pre-Phase-6 behavior: any number of sessions' jobs may run at once)."""

    cpu_threads: int
    """Passed as ``cpu_threads`` to both ``TranscriptionEngine``/``WhisperModel`` (via
    ctranslate2) and ``PyannoteSpeakerDiarizationEngine`` (via
    ``torch.set_num_threads()``) — the same conservative cap for whichever CPU-heavy
    model actually runs. ``0`` = each library's own default, unchanged from before."""

    diarization_allowed: bool
    """When ``False`` (Low Resource only), switching to this profile turns
    ``diarization_enabled`` off — loading a multi-hundred-MB model and running CPU
    inference on top of transcription is exactly the kind of concurrent heavy AI work
    this profile exists to avoid. This is a one-way override, not a remembered
    preference: switching back to Balanced/Quality does not automatically re-enable
    diarization, the user checks it again if they want it — simpler and more
    predictable than silently restoring a hidden prior state (see
    ``SettingsDialog._on_profile_changed``)."""


_EFFECTS: dict[ProcessingProfile, ProfileEffects] = {
    ProcessingProfile.LOW_RESOURCE: ProfileEffects(
        max_concurrent_ai_jobs=1, cpu_threads=2, diarization_allowed=False
    ),
    ProcessingProfile.BALANCED: ProfileEffects(
        max_concurrent_ai_jobs=0, cpu_threads=0, diarization_allowed=True
    ),
    ProcessingProfile.QUALITY: ProfileEffects(
        max_concurrent_ai_jobs=0, cpu_threads=0, diarization_allowed=True
    ),
}


def effects_for(profile: ProcessingProfile) -> ProfileEffects:
    return _EFFECTS[profile]


def recommended_whisper_model(profile: ProcessingProfile) -> str:
    return RECOMMENDED_WHISPER_MODEL[profile]
