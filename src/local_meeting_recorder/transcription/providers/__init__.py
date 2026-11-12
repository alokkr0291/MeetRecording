"""Cloud transcription provider clients (Phase 4.5): OpenAI and OpenRouter, both behind
``TranscriptionProviderClient`` (see ``protocol.py``) so ``transcription/manager.py``
never needs to know which provider it is driving — the same background-job, duplicate-
prevention, and retry logic in ``TranscriptionManager`` covers local and cloud alike.

Stdlib ``urllib`` only, no new HTTP dependency: both providers have a real, documented
audio-transcription endpoint (verified by inspecting their published API references, not
assumed), and both need ``response_format=verbose_json`` explicitly requested to get
timestamped segments — their default plain-text response has no timestamps at all.
"""

from __future__ import annotations
