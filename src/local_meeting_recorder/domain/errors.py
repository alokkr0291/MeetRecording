"""Exception hierarchy shared by all layers."""

from __future__ import annotations


class LMRError(Exception):
    """Base class for all application errors."""

    code: str = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    def __str__(self) -> str:
        return self.message


class InvalidTransitionError(LMRError):
    """Raised when the recording state machine is asked for an illegal transition."""

    code = "invalid_transition"


class CaptureError(LMRError):
    """Any failure originating in the capture layer or native helper."""

    code = "capture_error"


class PermissionDeniedError(CaptureError):
    """A required macOS permission (screen recording / microphone) is not granted."""

    code = "permission_denied"

    def __init__(self, message: str, *, permission: str) -> None:
        super().__init__(message)
        self.permission = permission


class HelperUnavailableError(CaptureError):
    """The native capture helper binary is missing or cannot be started."""

    code = "helper_unavailable"


class HelperCrashedError(CaptureError):
    """The native capture helper exited unexpectedly."""

    code = "helper_crashed"

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class HelperProtocolError(CaptureError):
    """The helper sent something we could not parse or did not answer in time."""

    code = "helper_protocol_error"


class InsufficientDiskSpaceError(LMRError):
    """Free disk space below the configured minimum."""

    code = "insufficient_disk_space"

    def __init__(self, message: str, *, free_mb: float, required_mb: float) -> None:
        super().__init__(message)
        self.free_mb = free_mb
        self.required_mb = required_mb


class MediaError(LMRError):
    """ffprobe/ffmpeg failure or an invalid media file."""

    code = "media_error"
