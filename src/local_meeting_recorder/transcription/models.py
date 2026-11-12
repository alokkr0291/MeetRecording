"""Pydantic schema for a finished transcription, persisted as ``transcript.json``."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_meeting_recorder.domain.session_metadata import utc_now

TRANSCRIPT_SCHEMA_VERSION = 1

_SOURCE_LABELS = {"microphone": "Mic", "system": "System"}

#: The microphone track is always exactly one speaker — the app's own user — never run
#: through diarization and never given a remote speaker identity (Part C's explicit
#: "never assign a remote speaker identity to the microphone track").
USER_SPEAKER_ID = "user"
USER_SPEAKER_DISPLAY_NAME = "You"

#: A system-audio segment whose diarization region didn't confidently match any
#: clustered speaker (or diarization never ran at all) gets this instead of a guess —
#: "do not invent participant identities."
UNKNOWN_SPEAKER_ID = "speaker_unknown"
UNKNOWN_SPEAKER_DISPLAY_NAME = "Unidentified speaker"


class AudioSource(StrEnum):
    """Which capture track a segment's audio came from (Phase 5: source-separated
    transcription) — matches ``domain.session_metadata.AudioTrackInfo.role``."""

    MICROPHONE = "microphone"
    SYSTEM = "system"


class Speaker(BaseModel):
    """One voice identity within a session (Phase 5C). ``speaker_id`` is stable only
    *within* this one recording — never used for cross-meeting voice recognition.
    ``display_name`` is the only field a user can edit (renaming); everything else is
    fixed by how the speaker was discovered."""

    model_config = ConfigDict(frozen=True)

    speaker_id: str
    display_name: str
    source_type: AudioSource
    confidence: float | None = None


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_s: float
    end_s: float
    text: str
    source: AudioSource | None = None
    """``None`` for a single combined transcript (Phase 4's mixed-track pipeline, or any
    transcript produced before Phase 5); set when the segment came from one isolated,
    independently transcribed source track."""
    speaker_id: str | None = None
    """Phase 5C: ``USER_SPEAKER_ID`` for every microphone segment (always, regardless of
    diarization), a clustered label (e.g. ``"speaker_1"``) for a system segment
    diarization confidently placed, ``UNKNOWN_SPEAKER_ID`` for one it could not place,
    and ``None`` when diarization has not run at all (or is disabled) — distinct from
    "unknown" so the UI can tell "we tried and couldn't tell" apart from "we never
    tried"."""


class Transcript(BaseModel):
    """The ordered result of transcribing one session's recording.

    ``segments`` must be in non-decreasing start-time order with ``end_s >= start_s`` on
    each one — faster-whisper already yields segments this way, but this is validated
    here too so a corrupt or hand-edited ``transcript.json`` is rejected on load rather
    than silently misrendered.
    """

    schema_version: int = TRANSCRIPT_SCHEMA_VERSION
    session_id: str
    language: str
    language_probability: float | None = None
    model_name: str
    created_at: datetime = Field(default_factory=utc_now)
    segments: list[TranscriptSegment] = Field(default_factory=list)
    source_errors: dict[AudioSource, str] = Field(default_factory=dict)
    """Phase 5 source-separated transcription: populated only when at least one source
    succeeded and at least one other failed (``TranscriptionStatus.PARTIAL``) — maps the
    failed source to its error message. Empty for a single combined transcript or a fully
    successful source-separated one."""
    speakers: list[Speaker] = Field(default_factory=list)
    """Phase 5C: empty unless diarization has run at least once. Always includes
    ``USER_SPEAKER_ID`` when any microphone segment exists; never invented — every entry
    corresponds to a voice diarization actually detected."""
    diarization_model: str | None = None
    """Which diarization pipeline produced ``speakers``/segment ``speaker_id``s, e.g.
    ``"pyannote/speaker-diarization-3.1"`` — processing metadata, ``None`` if diarization
    never ran."""
    revision: int = 0
    """Bumped by one every time a *new* transcription result is written for this session
    (not by a diarization enrichment, which only tags existing segments). Lets
    ``TranscriptionManager`` detect, at write time, whether a diarization job's in-memory
    snapshot of the transcript is still current or was superseded by a concurrent
    retry-transcription — see the write-race guard in ``transcription/manager.py``."""

    @model_validator(mode="after")
    def _validate_segment_order(self) -> Transcript:
        previous_start = float("-inf")
        for segment in self.segments:
            if segment.end_s < segment.start_s:
                raise ValueError(
                    f"segment end ({segment.end_s}) precedes its start ({segment.start_s})"
                )
            if segment.start_s < previous_start:
                raise ValueError("segments must be in non-decreasing start-time order")
            previous_start = segment.start_s
        return self

    @property
    def text(self) -> str:
        """Plain-text concatenation of every segment, in order — used for ``transcript.txt``.
        A segment with a resolved speaker (Phase 5C) is prefixed with that speaker's
        display name (e.g. ``"You"``, ``"Speaker 1"``); otherwise a source-tagged segment
        (Phase 5A/B) falls back to ``[System]``/``[Mic]``, so the two speakers stay
        distinguishable in the plain-text file even without diarization."""
        speaker_names = {speaker.speaker_id: speaker.display_name for speaker in self.speakers}
        lines = []
        for segment in self.segments:
            stripped = segment.text.strip()
            if not stripped:
                continue
            label = speaker_names.get(segment.speaker_id) if segment.speaker_id else None
            if label is None and segment.source is not None:
                label = _SOURCE_LABELS[segment.source.value]
            if label is not None:
                stripped = f"[{label}] {stripped}"
            lines.append(stripped)
        return "\n".join(lines)

    @property
    def text_with_segment_indices(self) -> str:
        """Like ``text``, but each line is prefixed with its position in ``segments``
        (e.g. ``"[7] ..."``) instead of a speaker label. Handed to a summary provider
        (Phase 5F) so it can cite *which* line(s) support a decision or action item via
        ``segment_refs`` — those numbers are the same ``segments`` list positions a
        reader (or future UI) resolves back to a real timestamp, never an invented
        reference scheme of its own. Blank segments are skipped from the rendered text
        exactly as ``text`` skips them, but the numbering still reflects their real
        position in ``segments`` so a cited index always resolves correctly.
        """
        speaker_names = {speaker.speaker_id: speaker.display_name for speaker in self.speakers}
        lines = []
        for i, segment in enumerate(self.segments):
            stripped = segment.text.strip()
            if not stripped:
                continue
            label = speaker_names.get(segment.speaker_id) if segment.speaker_id else None
            if label is None and segment.source is not None:
                label = _SOURCE_LABELS[segment.source.value]
            prefix = f"{label}: " if label else ""
            lines.append(f"[{i}] {prefix}{stripped}")
        return "\n".join(lines)
