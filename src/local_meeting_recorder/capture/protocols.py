"""Wire types and the platform-independent capture interface.

These types mirror what ``native/macos/Sources/main.swift`` actually sends and expects —
not the earlier, aspirational sketch in ``ARCHITECTURE.md`` (now corrected to match this
module; see ``native/macos/PROTOCOL.md`` for the authoritative protocol reference).

Known corrections versus the original design sketch:
  * the start command field is ``capture_microphone``, not ``capture_mic``.
  * ``started``/``stopped``/``stats`` carry no ``session_id``; the helper only ever knows
    the output path. Session identity is a Python-side concept (``domain.session_id``).
  * ``stats`` is a rich counter payload (``video_frames``, ``system_audio_buffers``, ...),
    not the ``{elapsed_s, frames, audio_bytes}`` shape the sketch described.
  * permissions are reported two ways: nested under ``probe`` (``{"probe": {...,
    "permissions": {...}}}``) for a passive check, or flat under ``permissions`` as the
    response to the (previously undocumented) ``request-permissions`` command, which also
    triggers the TCC prompts.
  * ``list-displays`` entries include ``pixel_width``/``pixel_height``/``is_main``.
  * a ``stopping`` event (with a ``reason``) always precedes ``stopped``.
  * every ``error`` event carries a ``fatal`` boolean; ``permission_denied`` additionally
    carries ``permission``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from local_meeting_recorder.domain.errors import (
    CaptureError,
    HelperProtocolError,
    PermissionDeniedError,
)

# -- Permissions ------------------------------------------------------------------------


class PermissionState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    RESTRICTED = "restricted"
    NOT_DETERMINED = "not_determined"

    @property
    def is_usable(self) -> bool:
        return self is PermissionState.GRANTED


class PermissionStatus(BaseModel):
    """What the helper's ``probe`` (or ``request-permissions``) command reported."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    screen: PermissionState
    screen_preflight: bool = False
    microphone: PermissionState

    @property
    def all_granted(self) -> bool:
        return self.screen.is_usable and self.microphone.is_usable


class PermissionsRequestResult(PermissionStatus):
    """Response to ``request-permissions``: a status snapshot plus what the prompts did."""

    screen_request_result: bool = False
    microphone_request_result: bool = False


# -- Displays / tracks --------------------------------------------------------------------


class DisplayInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    name: str
    width: int
    height: int
    pixel_width: int
    pixel_height: int
    is_main: bool = False


class TrackInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    index: int
    kind: str  # "video" | "audio"
    role: str | None = None  # "system" | "microphone", audio only
    codec: str | None = None


# -- Commands (Python -> helper) ---------------------------------------------------------


class StartOptions(BaseModel):
    """Everything the Python side may ask for when starting a recording.

    Field names and defaults match ``StartRequest`` in ``main.swift`` exactly.
    """

    model_config = ConfigDict(frozen=True)

    output_path: Path
    display_id: int | None = None
    capture_microphone: bool = True
    microphone_device_id: str | None = None
    fps: int = Field(default=30, ge=1, le=60)
    video_bitrate_kbps: int = Field(default=6000, ge=500)
    audio_bitrate_kbps: int = Field(default=128, ge=32, le=320)
    max_dimension: int = Field(default=1920, ge=320)
    shows_cursor: bool = True
    stats_interval_s: float = Field(default=5.0, gt=0)
    min_free_mb: float = 512.0

    def to_command(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cmd": "start",
            "output_path": str(self.output_path),
            "capture_microphone": self.capture_microphone,
            "fps": self.fps,
            "video_bitrate_kbps": self.video_bitrate_kbps,
            "audio_bitrate_kbps": self.audio_bitrate_kbps,
            "max_dimension": self.max_dimension,
            "shows_cursor": self.shows_cursor,
            "stats_interval_s": self.stats_interval_s,
            "min_free_mb": self.min_free_mb,
        }
        if self.display_id is not None:
            payload["display_id"] = self.display_id
        if self.microphone_device_id is not None:
            payload["microphone_device_id"] = self.microphone_device_id
        return payload


# -- Events (helper -> Python) -----------------------------------------------------------


class ReadyInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str
    protocol: int
    pid: int
    # The helper reports its host OS version under a platform-named key: the macOS
    # helper sends "macos", the Windows helper sends "windows". Informational only.
    macos: str | None = None
    windows: str | None = None


class ProbeInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str
    protocol: int
    macos: str | None = None
    windows: str | None = None
    permissions: PermissionStatus


class DisplaysInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    displays: list[DisplayInfo]


class StartedInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    output_path: str
    display_id: int
    width: int
    height: int
    fps: int
    capture_microphone: bool
    tracks: list[TrackInfo] = Field(default_factory=list)


class StatsInfo(BaseModel):
    """Periodic recorder counters. Offset/duration fields are absent until a session starts."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    elapsed_s: float
    bytes: int
    free_mb: float
    session_started: bool = False
    video_frames: int = 0
    video_skipped_incomplete: int = 0
    video_dropped_not_ready: int = 0
    system_audio_buffers: int = 0
    system_audio_dropped_early: int = 0
    system_audio_dropped_not_ready: int = 0
    mic_audio_buffers: int = 0
    mic_audio_dropped_early: int = 0
    mic_audio_dropped_not_ready: int = 0
    first_video_offset_s: float | None = None
    first_system_audio_offset_s: float | None = None
    first_mic_audio_offset_s: float | None = None
    media_duration_s: float | None = None


class WarningInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str
    message: str
    free_mb: float | None = None


class ErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str
    message: str
    fatal: bool = False
    permission: str | None = None
    domain: str | None = None
    os_code: int | None = None

    def to_exception(self) -> CaptureError:
        if self.code == "permission_denied":
            return PermissionDeniedError(self.message, permission=self.permission or "unknown")
        return CaptureError(self.message, code=self.code)


class StoppingInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    reason: str


class StoppedInfo(BaseModel):
    """Final report from ``finishWriter``. ``error`` is set only when ``finalized`` is false."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    reason: str
    finalized: bool
    output_path: str
    bytes: int
    duration_s: float
    elapsed_s: float
    error: str | None = None
    video_frames: int = 0
    system_audio_buffers: int = 0
    mic_audio_buffers: int = 0
    first_video_offset_s: float | None = None
    first_system_audio_offset_s: float | None = None
    first_mic_audio_offset_s: float | None = None
    media_duration_s: float | None = None


EVENT_MODELS: dict[str, type[BaseModel]] = {
    "ready": ReadyInfo,
    "probe": ProbeInfo,
    "permissions": PermissionsRequestResult,
    "displays": DisplaysInfo,
    "started": StartedInfo,
    "stats": StatsInfo,
    "warning": WarningInfo,
    "error": ErrorInfo,
    "stopping": StoppingInfo,
    "stopped": StoppedInfo,
}


def parse_event(payload: dict[str, Any]) -> tuple[str, BaseModel]:
    """Decode one helper stdout line's already-JSON-parsed object.

    Raises ``HelperProtocolError`` if ``event`` is missing/unknown or the payload does not
    match the expected shape — the caller decides whether that is fatal.
    """
    name = payload.get("event")
    if not isinstance(name, str):
        raise HelperProtocolError(f'helper line has no string "event" field: {payload!r}')
    model_cls = EVENT_MODELS.get(name)
    if model_cls is None:
        raise HelperProtocolError(f"unknown helper event {name!r}: {payload!r}")
    try:
        return name, model_cls.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError, narrowed by re-raising our own type
        raise HelperProtocolError(f"malformed {name!r} event: {exc}") from exc


# -- Callbacks ----------------------------------------------------------------------------


@dataclass
class CaptureCallbacks:
    """Optional hooks for events that arrive asynchronously, outside a command's response.

    ``on_stats``/``on_warning``/``on_stopping`` fire on the helper's reader thread; keep them
    fast and non-blocking (forward to a Qt signal, do not touch widgets directly).
    """

    on_stats: Callable[[StatsInfo], None] | None = None
    on_warning: Callable[[WarningInfo], None] | None = None
    on_stopping: Callable[[StoppingInfo], None] | None = None
    on_crash: Callable[[int | None, str], None] | None = None


# -- Backend protocol -----------------------------------------------------------------------


class CaptureBackend(Protocol):
    """Platform-independent interface the application layer talks to.

    Implementations: ``capture.macos.backend.MacOSCaptureBackend`` (real) and
    ``capture.fake.FakeCaptureBackend`` (tests). Nothing above this protocol may import
    macOS-specific modules or know about JSON-lines/subprocess details.
    """

    @property
    def is_recording(self) -> bool: ...

    def check_permissions(self, *, timeout: float = 5.0) -> PermissionStatus: ...

    def request_permissions(self, *, timeout: float = 30.0) -> PermissionsRequestResult: ...

    def list_displays(self, *, timeout: float = 5.0) -> list[DisplayInfo]: ...

    def start(self, options: StartOptions, *, timeout: float = 20.0) -> StartedInfo: ...

    def stop(self, *, timeout: float = 30.0) -> StoppedInfo: ...

    def shutdown(self) -> None: ...
