"""Pure domain types: no I/O, no platform code."""

from __future__ import annotations

from local_meeting_recorder.domain.errors import (
    CaptureError,
    HelperCrashedError,
    HelperProtocolError,
    HelperUnavailableError,
    InsufficientDiskSpaceError,
    InvalidTransitionError,
    LMRError,
    MediaError,
    PermissionDeniedError,
)
from local_meeting_recorder.domain.recording_state import (
    ALLOWED_TRANSITIONS,
    RecordingState,
    assert_transition,
    can_transition,
)
from local_meeting_recorder.domain.session_id import (
    SessionId,
    is_valid_session_id,
    new_session_id,
)
from local_meeting_recorder.domain.session_metadata import (
    AudioTrackInfo,
    PostProcessingArtifacts,
    SessionMetadata,
    SessionStatus,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AudioTrackInfo",
    "CaptureError",
    "HelperCrashedError",
    "HelperProtocolError",
    "HelperUnavailableError",
    "InsufficientDiskSpaceError",
    "InvalidTransitionError",
    "LMRError",
    "MediaError",
    "PermissionDeniedError",
    "PostProcessingArtifacts",
    "RecordingState",
    "SessionId",
    "SessionMetadata",
    "SessionStatus",
    "assert_transition",
    "can_transition",
    "is_valid_session_id",
    "new_session_id",
]
