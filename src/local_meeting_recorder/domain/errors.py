"""Exception hierarchy shared by all layers."""

from __future__ import annotations

#: Shared across every cloud provider factory (transcription's and summarization's) so
#: the UI (``ui/app_controller.py``) can recognize "this failure means go to Settings"
#: regardless of which subsystem raised it, with one piece of wiring.
MISSING_API_KEY_CODE = "missing_api_key"

_ERROR_DETAIL_MAX_LENGTH = 300


def sanitize_error_detail(detail: str, *, max_length: int = _ERROR_DETAIL_MAX_LENGTH) -> str:
    """Bound how much of a provider's raw HTTP error-response body ends up in an
    exception message (and therefore in logs). A provider error body is untrusted,
    provider-controlled text — it has no business being logged unbounded, and a
    multi-kilobyte HTML error page or a response that happens to echo back request
    content has no diagnostic value past the first couple hundred characters anyway.
    Collapses embedded newlines/control characters so one bad response can't fill the
    log with multiple fake "lines"."""
    collapsed = " ".join(detail.split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[:max_length] + "… (truncated)"


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


class NoAudioStreamError(MediaError):
    """A recording has no audio streams at all, so nothing can be transcribed."""

    code = "no_audio_stream"


class AudioExtractionError(MediaError):
    """ffmpeg failed to extract/mix down audio for transcription."""

    code = "audio_extraction_failed"


class TranscriptionError(LMRError):
    """A faster-whisper model failed to load or to transcribe audio."""

    code = "transcription_failed"


class TranscriptionEmptyAudioError(TranscriptionError):
    """The extracted audio has no frames at all (e.g. a silent/empty recording)."""

    code = "transcription_empty_audio"


class StorageDirectoryError(LMRError):
    """A user-chosen directory (e.g. the recordings storage location) does not exist,
    cannot be created, or is not writable."""

    code = "storage_directory_invalid"


class CredentialStoreError(LMRError):
    """A secure credential store operation failed, or no secure store is available on
    this platform. Never a reason to fall back to plaintext storage."""

    code = "credential_store_error"


class DiarizationError(LMRError):
    """A speaker diarization backend failed to load or to process audio. Never fatal:
    the transcript's segments are preserved exactly as transcribed, just without
    ``speaker_id`` resolved beyond the microphone track's fixed ``"user"``."""

    code = "diarization_failed"


class DiarizationUnavailableError(DiarizationError):
    """Diarization is enabled in Settings but its optional dependency isn't installed,
    or its gated model has no Hugging Face token configured — a setup problem, not a
    transient failure, so it gets its own code the UI can word differently."""

    code = "diarization_unavailable"


class SummaryError(LMRError):
    """A summary provider failed to load, connect, or return a usable structured
    result. Never touches the transcript or the recording."""

    code = "summary_failed"


class SummaryMalformedOutputError(SummaryError):
    """The provider responded, but its output did not validate against the expected
    structured schema (or wasn't valid JSON at all)."""

    code = "summary_malformed_output"
