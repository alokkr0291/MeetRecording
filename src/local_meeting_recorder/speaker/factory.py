"""Builds the ``SpeakerDiarizationEngine`` when diarization is enabled.

Mirrors ``transcription/providers/factory.py``'s shape: raises a typed error (here,
``DiarizationUnavailableError``) when a precondition is missing rather than silently
degrading — the caller (``TranscriptionManager``) turns that into an ordinary
diarization failure, never touching the transcript's already-successful segments.
"""

from __future__ import annotations

from local_meeting_recorder.domain.errors import DiarizationUnavailableError
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.speaker.protocol import SpeakerDiarizationEngine
from local_meeting_recorder.speaker.pyannote_engine import PyannoteSpeakerDiarizationEngine

HUGGINGFACE_TOKEN_CREDENTIAL = "huggingface_token"


def build_diarization_engine(
    credential_store: CredentialStore, *, cpu_threads: int | None = None
) -> SpeakerDiarizationEngine:
    token = credential_store.get(HUGGINGFACE_TOKEN_CREDENTIAL)
    if not token:
        raise DiarizationUnavailableError(
            "Diarization needs a Hugging Face access token. Open Settings to add one "
            "(free account; accept pyannote/speaker-diarization-3.1's terms once at "
            "huggingface.co)."
        )
    return PyannoteSpeakerDiarizationEngine(hf_token=token, cpu_threads=cpu_threads)
