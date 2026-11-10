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


class PostProcessingArtifacts(BaseModel):
    mixdown_file: str | None = None
    audio_16k_file: str | None = None
    transcript_file: str | None = None
    summary_file: str | None = None


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
