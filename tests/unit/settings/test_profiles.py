"""``settings/profiles.py``: pure lookup from ``ProcessingProfile`` to concrete,
CPU-first behavior — no I/O, no Qt."""

from __future__ import annotations

from local_meeting_recorder.settings.models import ProcessingProfile
from local_meeting_recorder.settings.profiles import effects_for, recommended_whisper_model


def test_low_resource_is_the_most_conservative() -> None:
    effects = effects_for(ProcessingProfile.LOW_RESOURCE)
    assert effects.max_concurrent_ai_jobs == 1
    assert effects.cpu_threads > 0
    assert effects.diarization_allowed is False
    assert recommended_whisper_model(ProcessingProfile.LOW_RESOURCE) == "tiny"


def test_balanced_is_unlimited_and_allows_diarization() -> None:
    effects = effects_for(ProcessingProfile.BALANCED)
    assert effects.max_concurrent_ai_jobs == 0
    assert effects.cpu_threads == 0
    assert effects.diarization_allowed is True
    assert recommended_whisper_model(ProcessingProfile.BALANCED) == "base"


def test_quality_is_unlimited_and_allows_diarization() -> None:
    effects = effects_for(ProcessingProfile.QUALITY)
    assert effects.max_concurrent_ai_jobs == 0
    assert effects.diarization_allowed is True
    assert recommended_whisper_model(ProcessingProfile.QUALITY) == "small"


def test_every_profile_has_effects_and_a_recommended_model() -> None:
    for profile in ProcessingProfile:
        effects_for(profile)
        assert recommended_whisper_model(profile)
