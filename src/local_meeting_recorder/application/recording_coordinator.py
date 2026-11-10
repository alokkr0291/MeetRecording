"""``RecordingCoordinator``: the recording state machine and session lifecycle owner.

Talks only to ``capture.protocols.CaptureBackend`` — never to the Swift helper, a
subprocess, or any macOS API directly — so it works identically with
``MacOSCaptureBackend`` and ``FakeCaptureBackend``.

States reuse ``domain.recording_state.RecordingState`` exactly (Idle, Preparing,
Recording, Finalizing, Saved, Error); ``start_recording``/``stop_recording`` raise on
failure (after persisting the failure and moving to Error) rather than returning a status
object, so a caller's ``try/except`` is also the state-machine contract.

Backend wiring: a ``CaptureBackend``'s event callbacks (``on_stats``/``on_warning``/
``on_crash``) are fixed at its own construction time (see ``capture/macos/backend.py``,
``capture/fake.py`` — Phase 2b, intentionally not touched here), so the coordinator cannot
attach itself to an already-built backend after the fact. Construct it with
``backend_factory``: a callable that receives the coordinator's own ``CaptureCallbacks``
and returns the backend to use. Example::

    coordinator = RecordingCoordinator(
        backend_factory=lambda callbacks: MacOSCaptureBackend(callbacks=callbacks),
        session_store=store,
        settings=settings,
    )
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

from local_meeting_recorder.application.recovery import recover_incomplete_sessions
from local_meeting_recorder.capture.protocols import (
    CaptureBackend,
    CaptureCallbacks,
    PermissionStatus,
    StartedInfo,
    StartOptions,
    StatsInfo,
    StoppedInfo,
    WarningInfo,
)
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import (
    InsufficientDiskSpaceError,
    MediaError,
    PermissionDeniedError,
)
from local_meeting_recorder.domain.recording_state import RecordingState, assert_transition
from local_meeting_recorder.domain.session_id import new_session_id
from local_meeting_recorder.domain.session_metadata import AudioTrackInfo, SessionMetadata
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.session_store import SessionStore

log = get_logger(__name__)


@dataclass
class CoordinatorCallbacks:
    """UI-facing hooks. Plain callables — no Qt, no other framework dependency."""

    on_state_changed: Callable[[RecordingState], None] | None = None
    on_stats: Callable[[StatsInfo], None] | None = None
    on_warning: Callable[[WarningInfo], None] | None = None


class RecordingCoordinator:
    def __init__(
        self,
        *,
        backend_factory: Callable[[CaptureCallbacks], CaptureBackend],
        session_store: SessionStore,
        settings: Settings,
        callbacks: CoordinatorCallbacks | None = None,
        recover_on_init: bool = True,
    ) -> None:
        self._store = session_store
        self._settings = settings
        self._callbacks = callbacks or CoordinatorCallbacks()
        self._lock = threading.RLock()
        self._state = RecordingState.IDLE
        self._metadata: SessionMetadata | None = None
        self._stop_requested = False
        self._last_error: tuple[str, str] | None = None
        self._backend = backend_factory(
            CaptureCallbacks(
                on_stats=self._on_backend_stats,
                on_warning=self._on_backend_warning,
                on_crash=self._on_backend_crash,
            )
        )
        self.recovered_sessions: list[SessionMetadata] = (
            recover_incomplete_sessions(session_store) if recover_on_init else []
        )

    # -- read-only status -----------------------------------------------------------------

    @property
    def state(self) -> RecordingState:
        with self._lock:
            return self._state

    @property
    def current_session(self) -> SessionMetadata | None:
        with self._lock:
            return self._metadata

    @property
    def last_error(self) -> tuple[str, str] | None:
        with self._lock:
            return self._last_error

    def check_permissions(self, *, timeout: float | None = None) -> PermissionStatus:
        """Query current permission status without starting a recording.

        Safe to call from any coordinator state (including while Recording); it never
        touches the state machine. The UI uses this to decide whether to show the
        permission guide before even offering the consent dialog.
        """
        return self._backend.check_permissions(
            timeout=timeout if timeout is not None else self._settings.helper_start_timeout_s
        )

    # -- commands ---------------------------------------------------------------------------

    def start_recording(
        self,
        *,
        display_id: int | None = None,
        capture_microphone: bool | None = None,
        title: str | None = None,
    ) -> SessionMetadata | None:
        """Begin a new recording. Returns the session, or ``None`` if cancelled by a
        concurrent ``stop_recording()`` before any media existed. Raises on failure
        (state moves to Error first)."""
        with self._lock:
            assert_transition(self._state, RecordingState.PREPARING)
            self._state = RecordingState.PREPARING
            self._metadata = None
            self._stop_requested = False
            self._last_error = None
        self._notify_state_changed()

        want_mic = (
            self._settings.capture_microphone if capture_microphone is None else capture_microphone
        )
        display = self._settings.display_id if display_id is None else display_id

        try:
            permissions = self._backend.check_permissions(
                timeout=self._settings.helper_start_timeout_s
            )
        except Exception as exc:
            self._fail_preparing(exc, code=getattr(exc, "code", "capture_error"), message=str(exc))

        if not permissions.screen.is_usable:
            self._fail_preparing(
                PermissionDeniedError(
                    "Screen Recording permission is not granted", permission="screen"
                ),
                code="permission_denied",
                message="Screen Recording permission is not granted",
            )
        if want_mic and not permissions.microphone.is_usable:
            self._fail_preparing(
                PermissionDeniedError(
                    "Microphone permission is not granted", permission="microphone"
                ),
                code="permission_denied",
                message="Microphone permission is not granted",
            )

        free_mb = self._store.paths.free_space_mb()
        if free_mb < self._settings.min_free_disk_mb:
            message = (
                f"only {free_mb:.0f} MB free; need at least {self._settings.min_free_disk_mb} MB"
            )
            self._fail_preparing(
                InsufficientDiskSpaceError(
                    message, free_mb=free_mb, required_mb=float(self._settings.min_free_disk_mb)
                ),
                code="insufficient_disk_space",
                message=message,
            )

        with self._lock:
            cancelled_early = self._stop_requested
        if cancelled_early:
            self._cancel_before_backend_start(None)
            return None

        metadata = SessionMetadata(
            session_id=new_session_id(),
            display_id=display,
            capture_microphone=want_mic,
            title=title,
        )
        try:
            self._store.create(metadata)
        except OSError as exc:
            self._fail_preparing(exc, code="session_create_failed", message=str(exc))

        with self._lock:
            self._metadata = metadata
            cancelled_after_create = self._stop_requested
        if cancelled_after_create:
            self._cancel_before_backend_start(metadata)
            return None

        options = StartOptions(
            output_path=self._store.recording_path(metadata),
            display_id=display,
            capture_microphone=want_mic,
            fps=self._settings.video_fps,
            video_bitrate_kbps=self._settings.video_bitrate_kbps,
            stats_interval_s=self._settings.stats_interval_s,
            min_free_mb=float(self._settings.min_free_disk_mb),
        )
        try:
            started = self._backend.start(options, timeout=self._settings.helper_start_timeout_s)
        except Exception as exc:
            self._fail_preparing(exc, code=getattr(exc, "code", "capture_error"), message=str(exc))

        with self._lock:
            cancelled_after_start = self._stop_requested
        if cancelled_after_start:
            return self._cancel_after_backend_started(metadata, started)

        with self._lock:
            metadata.mark_started()
            metadata.audio_tracks = self._tracks_from_started(started)
            self._store.write(metadata)
            self._state = RecordingState.RECORDING
        self._notify_state_changed()
        return metadata

    def stop_recording(self) -> SessionMetadata | None:
        """Stop the active recording. Raises on failure (state moves to Error first).

        If called while still Preparing, this only flags the cancellation and returns
        ``None`` immediately — the in-flight ``start_recording()`` call owns every
        transition out of Preparing, since only it knows how far the backend got.
        """
        with self._lock:
            if self._state is RecordingState.PREPARING:
                self._stop_requested = True
                return None
            assert_transition(self._state, RecordingState.FINALIZING)
            self._state = RecordingState.FINALIZING
            metadata = self._metadata
        assert metadata is not None
        self._notify_state_changed()

        try:
            stopped = self._backend.stop(timeout=self._settings.helper_stop_timeout_s)
        except Exception as exc:
            self._fail_finalizing(
                metadata, exc, code=getattr(exc, "code", "capture_error"), message=str(exc)
            )

        return self._finalize_success(metadata, stopped)

    def reset(self) -> None:
        """Acknowledge Saved/Error and return to Idle. No-op if already Idle."""
        with self._lock:
            if self._state is RecordingState.IDLE:
                return
            assert_transition(self._state, RecordingState.IDLE)
            self._state = RecordingState.IDLE
            self._metadata = None
        self._notify_state_changed()

    def shutdown(self) -> None:
        """Best-effort stop for application exit. Never raises."""
        with self._lock:
            state = self._state
        if state is RecordingState.RECORDING:
            try:
                self.stop_recording()
            except Exception:
                log.exception("shutdown: stop_recording failed")
        try:
            self._backend.shutdown()
        except Exception:
            log.exception("shutdown: backend.shutdown failed")

    # -- outcome handlers -------------------------------------------------------------------

    def _fail_preparing(self, exc: Exception, *, code: str, message: str) -> NoReturn:
        with self._lock:
            if self._metadata is not None:
                self._metadata.mark_failed(code=code, message=message)
                self._store.write(self._metadata)
            self._last_error = (code, message)
            self._state = RecordingState.ERROR
        self._notify_state_changed()
        log.error("start_recording failed", extra={"code": code, "error_message": message})
        raise exc

    def _fail_finalizing(
        self, metadata: SessionMetadata, exc: Exception, *, code: str, message: str
    ) -> NoReturn:
        with self._lock:
            metadata.mark_failed(code=code, message=message)
            self._store.write(metadata)
            self._last_error = (code, message)
            self._state = RecordingState.ERROR
            self._metadata = metadata
        self._notify_state_changed()
        log.error(
            "stop_recording failed",
            extra={"session_id": metadata.session_id, "code": code, "error_message": message},
        )
        raise exc

    def _cancel_before_backend_start(self, metadata: SessionMetadata | None) -> None:
        with self._lock:
            if metadata is not None:
                metadata.mark_cancelled()
                self._store.write(metadata)
            self._state = RecordingState.IDLE
            self._metadata = None
            self._stop_requested = False
        self._notify_state_changed()
        log.info(
            "start_recording cancelled before backend start",
            extra={"session_id": metadata.session_id if metadata else None},
        )

    def _cancel_after_backend_started(
        self, metadata: SessionMetadata, started: StartedInfo
    ) -> SessionMetadata:
        with self._lock:
            metadata.mark_started()
            metadata.audio_tracks = self._tracks_from_started(started)
            self._store.write(metadata)
            self._state = RecordingState.FINALIZING
        self._notify_state_changed()
        log.info(
            "start_recording: stop requested mid-start; finalizing immediately",
            extra={"session_id": metadata.session_id},
        )
        try:
            stopped = self._backend.stop(timeout=self._settings.helper_stop_timeout_s)
        except Exception as exc:
            self._fail_finalizing(
                metadata, exc, code=getattr(exc, "code", "capture_error"), message=str(exc)
            )
        return self._finalize_success(metadata, stopped)

    def _finalize_success(self, metadata: SessionMetadata, stopped: StoppedInfo) -> SessionMetadata:
        output_path = self._store.recording_path(metadata)
        if not output_path.is_file():
            exc = MediaError(f"expected recording file at {output_path} but it does not exist")
            self._fail_finalizing(metadata, exc, code="missing_output_file", message=str(exc))
        with self._lock:
            metadata.mark_saved(duration_s=stopped.duration_s, video_frames=stopped.video_frames)
            self._store.write(metadata)
            self._metadata = metadata
            self._state = RecordingState.SAVED
        self._notify_state_changed()
        log.info(
            "recording saved",
            extra={"session_id": metadata.session_id, "duration_s": stopped.duration_s},
        )
        return metadata

    @staticmethod
    def _tracks_from_started(started: StartedInfo) -> list[AudioTrackInfo]:
        return [
            AudioTrackInfo(index=t.index, role=t.role or "unknown", codec=t.codec)
            for t in started.tracks
            if t.kind == "audio"
        ]

    # -- backend callbacks (fire on the backend's own thread) -------------------------------

    def _on_backend_stats(self, stats: StatsInfo) -> None:
        if self._callbacks.on_stats is not None:
            self._callbacks.on_stats(stats)

    def _on_backend_warning(self, warning: WarningInfo) -> None:
        log.warning(
            "capture warning", extra={"code": warning.code, "warning_message": warning.message}
        )
        if self._callbacks.on_warning is not None:
            self._callbacks.on_warning(warning)

    def _on_backend_crash(self, returncode: int | None, message: str) -> None:
        with self._lock:
            if self._state is not RecordingState.RECORDING:
                # Already finalizing/idle/error by some other path; nothing to reconcile.
                return
            metadata = self._metadata
            if metadata is not None:
                metadata.mark_failed(code="helper_crashed", message=message)
                self._store.write(metadata)
            self._last_error = ("helper_crashed", message)
            self._state = RecordingState.ERROR
        self._notify_state_changed()
        log.error(
            "capture backend crashed during recording",
            extra={"returncode": returncode, "error_message": message},
        )
        try:
            self._backend.shutdown()
        except Exception:
            log.exception("backend.shutdown after crash failed")

    # -- internals ----------------------------------------------------------------------------

    def _notify_state_changed(self) -> None:
        if self._callbacks.on_state_changed is not None:
            self._callbacks.on_state_changed(self.state)
