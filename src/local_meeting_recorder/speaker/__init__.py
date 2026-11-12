"""Speaker diarization (Phase 5C): identify distinct voices within the *system* audio
track only — the microphone track is always exactly one speaker (the app's own user),
never run through diarization, never given a remote identity. See
``transcription/models.py``'s ``Speaker``/``USER_SPEAKER_ID`` and
``speaker/protocol.py``'s ``SpeakerDiarizationEngine``.
"""

from __future__ import annotations
