"""Schema for ``session.json`` — the recoverable record of one recording session."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SESSION_SCHEMA_VERSION = 1
RECORDING_FILE_NAME = "recording.mov"
HELPER_LOG_FILE_NAME = "helper.log"


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SAVED = "saved"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"
    UNRECOVERABLE = "unrecoverable"

    @property
    def is_terminal(self) -> bool:
        return self is not SessionStatus.IN_PROGRESS


class AudioTrackInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    role: str  # "system" | "microphone"
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None


class TranscriptionStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    PARTIAL = "partial"
    """Phase 5 source-separated transcription: some audio sources (e.g. system) were
    transcribed successfully but at least one other (e.g. microphone) failed. The
    transcript file still exists with whichever segments succeeded; see
    ``Transcript.source_errors`` for which source(s) failed and why."""


class DiarizationStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DISABLED = "disabled"
    """The user has diarization turned off in Settings — distinct from ``FAILED`` so the
    UI can say "turned off" instead of "something went wrong."""


class SummaryStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class PostProcessingArtifacts(BaseModel):
    mixdown_file: str | None = None
    audio_16k_file: str | None = None
    transcript_file: str | None = None
    transcript_status: TranscriptionStatus = TranscriptionStatus.NOT_STARTED
    transcript_language: str | None = None
    transcript_model: str | None = None
    transcript_error: str | None = None
    diarization_status: DiarizationStatus = DiarizationStatus.NOT_STARTED
    diarization_model: str | None = None
    diarization_error: str | None = None
    diarization_has_unknown_speaker: bool = False
    """``True`` when at least one system-audio segment could not be confidently
    attributed to a clustered speaker (``UNKNOWN_SPEAKER_ID``) — lets the UI say
    "speakers partially identified" instead of implying every voice was resolved."""
    summary_file: str | None = None
    summary_status: SummaryStatus = SummaryStatus.NOT_STARTED
    summary_provider: str | None = None
    summary_model: str | None = None
    summary_error: str | None = None


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


class SessionMetadata(BaseModel):
    """Persisted at session start and updated on every lifecycle milestone.

    ``status`` stays ``in_progress`` until finalization; a session found in that state on
    the next launch is incomplete and must be surfaced to the user, never deleted silently.
    """

    schema_version: int = SESSION_SCHEMA_VERSION
    session_id: str
    status: SessionStatus = SessionStatus.IN_PROGRESS
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finalized_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    display_id: int | None = None
    capture_microphone: bool = True
    recording_file: str = RECORDING_FILE_NAME
    helper_log_file: str = HELPER_LOG_FILE_NAME
    duration_s: float | None = None
    video_frames: int | None = None
    audio_tracks: list[AudioTrackInfo] = Field(default_factory=list)
    post_processing: PostProcessingArtifacts = Field(default_factory=PostProcessingArtifacts)
    app_version: str | None = None
    helper_version: str | None = None
    os_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    title: str | None = None

    def mark_started(self, *, helper_version: str | None = None) -> None:
        self.started_at = utc_now()
        self.last_heartbeat_at = self.started_at
        if helper_version:
            self.helper_version = helper_version

    def heartbeat(self) -> None:
        self.last_heartbeat_at = utc_now()

    def mark_saved(
        self,
        *,
        duration_s: float | None,
        video_frames: int | None,
        audio_tracks: list[AudioTrackInfo] | None = None,
    ) -> None:
        self.status = SessionStatus.SAVED
        self.finalized_at = utc_now()
        self.duration_s = duration_s
        self.video_frames = video_frames
        if audio_tracks is not None:
            self.audio_tracks = audio_tracks
        self.error_code = None
        self.error_message = None

    def mark_failed(self, *, code: str, message: str) -> None:
        self.status = SessionStatus.FAILED
        self.finalized_at = utc_now()
        self.error_code = code
        self.error_message = message

    def mark_cancelled(self) -> None:
        self.status = SessionStatus.CANCELLED
        self.finalized_at = utc_now()

    def mark_transcription_running(self) -> None:
        self.post_processing.transcript_status = TranscriptionStatus.RUNNING
        self.post_processing.transcript_error = None

    def mark_transcription_done(self, *, transcript_file: str, language: str, model: str) -> None:
        self.post_processing.transcript_status = TranscriptionStatus.DONE
        self.post_processing.transcript_file = transcript_file
        self.post_processing.transcript_language = language
        self.post_processing.transcript_model = model
        self.post_processing.transcript_error = None

    def mark_transcription_failed(self, *, message: str) -> None:
        self.post_processing.transcript_status = TranscriptionStatus.FAILED
        self.post_processing.transcript_error = message

    def mark_transcription_partial(
        self, *, transcript_file: str, language: str, model: str, message: str
    ) -> None:
        """Some audio source(s) transcribed successfully, at least one did not."""
        self.post_processing.transcript_status = TranscriptionStatus.PARTIAL
        self.post_processing.transcript_file = transcript_file
        self.post_processing.transcript_language = language
        self.post_processing.transcript_model = model
        self.post_processing.transcript_error = message

    def mark_diarization_running(self) -> None:
        self.post_processing.diarization_status = DiarizationStatus.RUNNING
        self.post_processing.diarization_error = None
        self.post_processing.diarization_has_unknown_speaker = False

    def mark_diarization_done(self, *, model: str, has_unknown_speaker: bool = False) -> None:
        self.post_processing.diarization_status = DiarizationStatus.DONE
        self.post_processing.diarization_model = model
        self.post_processing.diarization_error = None
        self.post_processing.diarization_has_unknown_speaker = has_unknown_speaker

    def mark_diarization_failed(self, *, message: str) -> None:
        self.post_processing.diarization_status = DiarizationStatus.FAILED
        self.post_processing.diarization_error = message

    def mark_diarization_disabled(self) -> None:
        self.post_processing.diarization_status = DiarizationStatus.DISABLED
        self.post_processing.diarization_error = None

    def mark_summary_running(self) -> None:
        self.post_processing.summary_status = SummaryStatus.RUNNING
        self.post_processing.summary_error = None

    def mark_summary_done(self, *, provider: str, model: str) -> None:
        self.post_processing.summary_status = SummaryStatus.DONE
        self.post_processing.summary_file = "summary.json"
        self.post_processing.summary_provider = provider
        self.post_processing.summary_model = model
        self.post_processing.summary_error = None

    def mark_summary_failed(self, *, message: str) -> None:
        self.post_processing.summary_status = SummaryStatus.FAILED
        self.post_processing.summary_error = message
