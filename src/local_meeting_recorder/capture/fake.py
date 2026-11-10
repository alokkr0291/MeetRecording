"""In-process fake of ``CaptureBackend`` for tests. Never shipped as a feature.

No subprocess, no macOS APIs — just enough scripted behaviour to drive the future
``RecordingCoordinator`` (Phase 3) and to exercise ``CaptureBackend`` contract tests here
in Phase 2b without real hardware. Configure the public attributes before calling a
method; each method call is recorded for assertions.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from local_meeting_recorder.capture.protocols import (
    CaptureCallbacks,
    DisplayInfo,
    PermissionsRequestResult,
    PermissionState,
    PermissionStatus,
    StartedInfo,
    StartOptions,
    StatsInfo,
    StoppedInfo,
    TrackInfo,
    WarningInfo,
)
from local_meeting_recorder.domain.errors import CaptureError

_DEFAULT_DISPLAY = DisplayInfo(
    id=1,
    name="Fake Display",
    width=1920,
    height=1080,
    pixel_width=1920,
    pixel_height=1080,
    is_main=True,
)


@dataclass
class FakeCaptureBackend:
    callbacks: CaptureCallbacks = field(default_factory=CaptureCallbacks)
    permissions: PermissionStatus = field(
        default_factory=lambda: PermissionStatus(
            screen=PermissionState.GRANTED,
            screen_preflight=True,
            microphone=PermissionState.GRANTED,
        )
    )
    displays: list[DisplayInfo] = field(default_factory=lambda: [_DEFAULT_DISPLAY])

    # Scripted failures/delays, set by the test before calling the method they affect.
    fail_start_with: Exception | None = None
    fail_stop_with: Exception | None = None
    start_delay_s: float = 0.0

    # Call log for assertions.
    start_calls: list[StartOptions] = field(default_factory=list, init=False)
    stop_calls: int = field(default=0, init=False)
    shutdown_calls: int = field(default=0, init=False)

    _recording: bool = field(default=False, init=False)
    _last_options: StartOptions | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def is_recording(self) -> bool:
        return self._recording

    def check_permissions(self, *, timeout: float = 5.0) -> PermissionStatus:
        return self.permissions

    def request_permissions(self, *, timeout: float = 30.0) -> PermissionsRequestResult:
        return PermissionsRequestResult(
            screen=self.permissions.screen,
            screen_preflight=self.permissions.screen_preflight,
            microphone=self.permissions.microphone,
            screen_request_result=self.permissions.screen.is_usable,
            microphone_request_result=self.permissions.microphone.is_usable,
        )

    def list_displays(self, *, timeout: float = 5.0) -> list[DisplayInfo]:
        return list(self.displays)

    def start(self, options: StartOptions, *, timeout: float = 20.0) -> StartedInfo:
        self.start_calls.append(options)
        if self.start_delay_s:
            time.sleep(self.start_delay_s)
        if self.fail_start_with is not None:
            raise self.fail_start_with
        with self._lock:
            if self._recording:
                raise CaptureError("start() called while already recording")
            self._recording = True
            self._last_options = options
        # Mirrors AVAssetWriter's real behaviour: the output file exists from the moment
        # the writer is set up, before any media is written.
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        options.output_path.touch(exist_ok=True)
        display_id = options.display_id if options.display_id is not None else self.displays[0].id
        tracks = [
            TrackInfo(index=0, kind="video", codec="h264"),
            TrackInfo(index=1, kind="audio", role="system", codec="aac"),
        ]
        if options.capture_microphone:
            tracks.append(TrackInfo(index=2, kind="audio", role="microphone", codec="aac"))
        return StartedInfo(
            output_path=str(options.output_path),
            display_id=display_id,
            width=1920,
            height=1080,
            fps=options.fps,
            capture_microphone=options.capture_microphone,
            tracks=tracks,
        )

    def stop(self, *, timeout: float = 30.0) -> StoppedInfo:
        self.stop_calls += 1
        with self._lock:
            if not self._recording:
                raise CaptureError("stop() called with no active recording")
            self._recording = False
            options = self._last_options
        if self.fail_stop_with is not None:
            raise self.fail_stop_with
        return StoppedInfo(
            reason="requested",
            finalized=True,
            output_path=str(options.output_path) if options else "",
            bytes=1_048_576,
            duration_s=1.0,
            elapsed_s=1.0,
            video_frames=30,
            system_audio_buffers=50,
            mic_audio_buffers=50 if options is None or options.capture_microphone else 0,
        )

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        with self._lock:
            self._recording = False

    # -- test helpers: simulate events a real helper could emit mid-recording -----------

    def emit_stats(self, **overrides: object) -> None:
        if self.callbacks.on_stats is None:
            return
        defaults: dict[str, object] = {"elapsed_s": 1.0, "bytes": 1024, "free_mb": 10_000.0}
        defaults.update(overrides)
        self.callbacks.on_stats(StatsInfo.model_validate(defaults))

    def emit_warning(self, code: str, message: str, *, free_mb: float | None = None) -> None:
        if self.callbacks.on_warning is not None:
            self.callbacks.on_warning(WarningInfo(code=code, message=message, free_mb=free_mb))

    def simulate_crash(
        self, *, returncode: int | None = None, message: str = "simulated crash"
    ) -> None:
        """Mimic the helper process dying unexpectedly while a recording is active."""
        with self._lock:
            self._recording = False
        if self.callbacks.on_crash is not None:
            self.callbacks.on_crash(returncode, message)
