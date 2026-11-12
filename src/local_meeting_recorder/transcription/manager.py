"""Runs audio extraction + transcription as a background job, one per session.

Wired in from ``ui/app_controller.py`` after ``RecordingCoordinator`` reaches
``RecordingState.SAVED`` (see ``AppController._maybe_start_transcription``). This module
knows nothing about Qt, the coordinator, or the capture backend — only a session's
metadata and the recording path ``SessionStore`` already resolves. A transcription
failure never touches the session's own ``status``: the recording stays ``saved``
regardless of what happens here (see ``domain/session_metadata.py``'s
``mark_transcription_*`` methods, which only ever touch ``post_processing``).

Phase 5: when a session's recording has both a system and a microphone audio track
(``metadata.audio_tracks`` — true for essentially every real recording, verified against
actual hardware capture; see IMPLEMENTATION_STATUS.md's Phase 5 "Part A" section), each
track is extracted and transcribed *independently* (``_run_source_separated``) and the
results merged onto one chronological timeline, each segment tagged with its source. A
session with only one audio track (e.g. microphone capture was disabled) falls back to
the original Phase 4 mixdown-based single pass (``_run_mixed``) — nothing to separate.
One source failing does not fail the other: the job finishes ``partial`` rather than
``failed`` when at least one source succeeded. Every microphone segment is immediately
tagged with the fixed ``USER_SPEAKER_ID`` (Phase 5C) — unconditional, independent of
whether diarization ever runs.

Phase 5C: speaker diarization (identifying individual voices *within* the system
track) is a separate, independently retriable background job
(``diarize_async``/``_run_diarization``) that re-reads an already-written transcript and
only updates its system segments' ``speaker_id`` — it never re-transcribes, and a
diarization failure never touches the transcript's text/timing, only leaves
``speaker_id`` at ``None`` for the segments diarization didn't get to.

Source attribution for the *mixed* (single-pass) pipeline: when a recording has exactly
one audio track with a recognized role (microphone capture disabled -> system-only, or
the reverse), that one track *is* the whole recording, so its segments are tagged with
that source — this is what makes a system-only recording eligible for diarization at
all (see ``_single_known_source``). When a recording's tracks don't resolve to exactly
one known role (no track metadata at all, or multiple tracks of unclear role that
``AudioExtractor.extract_wav16k_mono`` may have mixed together with ``amix``), segments
are left unattributed (``source=None``) rather than guessing — diarization on an
unattributed transcript cleanly fails instead of silently tagging zero segments and
reporting success (a real bug this replaces, see IMPLEMENTATION_STATUS.md).

Write-race safety: ``_run_diarization`` captures the transcript's ``revision`` when it
starts, then — after the (slow) diarization pipeline runs — re-reads the transcript
under a per-session lock immediately before writing. If a retry-transcription finished
and wrote a newer revision in the meantime, the diarization job aborts with a clear
"stale" failure instead of overwriting the newer result with speaker tags computed
against stale segments. The same lock serializes a transcription job's own write against
a concurrent diarization write.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import DiarizationError, DiarizationUnavailableError
from local_meeting_recorder.domain.session_metadata import AudioTrackInfo, SessionMetadata
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.media.audio_extractor import AudioExtractor
from local_meeting_recorder.speaker.assignment import assign_system_speakers, assign_user_speaker
from local_meeting_recorder.speaker.protocol import DiarizationResult, SpeakerDiarizationEngine
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.transcription.engine import OnSegment, TranscriptionEngine
from local_meeting_recorder.transcription.models import (
    UNKNOWN_SPEAKER_ID,
    AudioSource,
    Speaker,
    Transcript,
    TranscriptSegment,
)
from local_meeting_recorder.transcription.providers.protocol import TranscriptionProviderClient
from local_meeting_recorder.transcription.store import TRANSCRIPT_JSON_NAME, TranscriptStore

log = get_logger(__name__)

# Local faster-whisper or a cloud client (Phase 4.5) — both satisfy
# ``TranscriptionProviderClient``, so everything below is provider-agnostic.
EngineFactory = Callable[[], TranscriptionProviderClient]
DiarizationEngineFactory = Callable[[], SpeakerDiarizationEngine]

# System before microphone: matches the real helper's track order (index 1, index 2 —
# see native/macos/PROTOCOL.md) and reads naturally in a merged transcript.
_SOURCE_ORDER: tuple[AudioSource, ...] = (AudioSource.SYSTEM, AudioSource.MICROPHONE)


@dataclass(frozen=True)
class TranscriptionProgress:
    session_id: str
    processed_s: float
    total_s: float | None


@dataclass
class TranscriptionCallbacks:
    """UI-facing hooks. Plain callables — no Qt, same shape as ``CoordinatorCallbacks``."""

    on_started: Callable[[str], None] | None = None
    on_progress: Callable[[TranscriptionProgress], None] | None = None
    on_completed: Callable[[str, Transcript], None] | None = None
    on_failed: Callable[[str, str, str], None] | None = None  # session_id, code, message
    on_diarization_started: Callable[[str], None] | None = None
    on_diarization_completed: Callable[[str, Transcript], None] | None = None
    on_diarization_failed: Callable[[str, str, str], None] | None = None  # id, code, message


@dataclass
class _SourceResult:
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = "unknown"
    language_probability: float | None = None
    error: str | None = None


@dataclass
class _DiarizationComputation:
    """Everything the (slow) compute phase of a diarization job produced, needed by the
    later, lock-guarded persist phase. Keeping these separate is what lets
    ``_merge_and_persist_diarization`` re-read the transcript fresh and detect a
    concurrent retry-transcription without re-running the diarization pipeline itself.
    """

    session_dir: Path
    base_revision: int
    system_positions: list[int]
    tagged_system: list[TranscriptSegment]
    system_speakers: list[Speaker]
    result: DiarizationResult


class TranscriptionManager:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        settings: Settings,
        callbacks: TranscriptionCallbacks | None = None,
        engine_factory: EngineFactory | None = None,
        audio_extractor: AudioExtractor | None = None,
        transcript_store: TranscriptStore | None = None,
        diarization_engine_factory: DiarizationEngineFactory | None = None,
        resource_governor: ResourceGovernor | None = None,
    ) -> None:
        self._store = session_store
        self._settings = settings
        self._callbacks = callbacks or TranscriptionCallbacks()
        # Caps how many sessions' heavy jobs (this manager's transcription/diarization,
        # or SummaryManager's) may run at once, app-wide — see
        # infrastructure/resource_governor.py. Unlimited (today's pre-Phase-6 behavior)
        # unless the caller (app.py, driven by the active processing profile) passes one.
        self._resource_governor = resource_governor or ResourceGovernor()
        self._engine_factory = engine_factory or (
            lambda: TranscriptionEngine(
                model_size=settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
        )
        self._extractor = audio_extractor or AudioExtractor(
            ffmpeg_path=settings.ffmpeg_path, ffprobe_path=settings.ffprobe_path
        )
        self._transcript_store = transcript_store or TranscriptStore()
        self._diarization_engine_factory = diarization_engine_factory
        self._engine: TranscriptionProviderClient | None = None
        self._diarization_engine: SpeakerDiarizationEngine | None = None
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._diarizing: set[str] = set()
        self._session_locks: dict[str, threading.Lock] = {}

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._running

    def _session_lock(self, session_id: str) -> threading.Lock:
        """One lock per session, guarding only the read-current-revision-then-write
        critical section of ``transcript.json`` — never held across the slow
        extraction/transcription/diarization work itself, only around the brief
        check-then-persist step that must not race."""
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def is_diarizing(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._diarizing

    def invalidate_cached_engine(self) -> None:
        """Call after the active provider, model, or API key changes (e.g. the user
        saves new Settings) so the next job builds a fresh client via ``engine_factory``
        instead of reusing one built for the old configuration. Clears both the
        transcription and the diarization engine caches."""
        self._engine = None
        self._diarization_engine = None

    def transcribe_async(self, metadata: SessionMetadata, *, language: str | None = None) -> bool:
        """Start a background job for ``metadata``'s session.

        Returns ``False`` without doing anything if a job for this session is already
        running — duplicate-prevention; the caller (UI) decides whether that's worth
        surfacing. Safe to call again after a failure or a success (a "retry"/"re-run" —
        re-does every source from scratch; there is no partial-retry of just the source
        that failed, see IMPLEMENTATION_STATUS.md).
        """
        with self._lock:
            if metadata.session_id in self._running:
                return False
            self._running.add(metadata.session_id)
        thread = threading.Thread(
            target=self._run,
            args=(metadata, language),
            name=f"lmr-transcribe-{metadata.session_id}",
            daemon=True,
        )
        thread.start()
        return True

    def diarize_async(self, metadata: SessionMetadata) -> bool:
        """Start a background diarization job for an *already-transcribed* session —
        independent of ``transcribe_async``: re-extracts only the system track, never
        re-transcribes, and only ever updates ``speaker_id`` on the existing transcript's
        system segments. Returns ``False`` if a diarization job for this session is
        already running (duplicate-prevention, its own set — diarizing and transcribing
        the same session concurrently is not prevented against each other, only against
        themselves, since they touch different things until the final write)."""
        with self._lock:
            if metadata.session_id in self._diarizing:
                return False
            self._diarizing.add(metadata.session_id)
        thread = threading.Thread(
            target=self._run_diarization,
            args=(metadata,),
            name=f"lmr-diarize-{metadata.session_id}",
            daemon=True,
        )
        thread.start()
        return True

    # -- internals ------------------------------------------------------------------------

    def _run(self, metadata: SessionMetadata, language: str | None) -> None:
        session_id = metadata.session_id
        self._mark_running(metadata)
        self._safe_callback(self._callbacks.on_started, session_id)
        try:
            tracks = self._tracks_by_source(metadata)
            if AudioSource.SYSTEM in tracks and AudioSource.MICROPHONE in tracks:
                self._run_source_separated(metadata, language, tracks)
            else:
                self._run_mixed(metadata, language)
        finally:
            with self._lock:
                self._running.discard(session_id)

    @staticmethod
    def _tracks_by_source(metadata: SessionMetadata) -> dict[AudioSource, AudioTrackInfo]:
        tracks: dict[AudioSource, AudioTrackInfo] = {}
        for track in metadata.audio_tracks:
            if track.role == AudioSource.SYSTEM.value:
                tracks[AudioSource.SYSTEM] = track
            elif track.role == AudioSource.MICROPHONE.value:
                tracks[AudioSource.MICROPHONE] = track
        return tracks

    def _run_mixed(self, metadata: SessionMetadata, language: str | None) -> None:
        """Phase 4 fallback: the recording's tracks don't resolve to both a system and
        a microphone source (e.g. microphone capture was disabled, or legacy/unknown
        track metadata), so there is nothing to source-separate."""
        session_id = metadata.session_id
        try:
            recording_path = self._store.recording_path(metadata)
            with tempfile.TemporaryDirectory(prefix=f"lmr-transcribe-{session_id}-") as tmp:
                wav_path = Path(tmp) / "audio_16k_mono.wav"
                self._extractor.extract_wav16k_mono(recording_path, wav_path)
                engine = self._get_engine()
                with self._resource_governor.heavy_job():
                    result = engine.transcribe(
                        wav_path,
                        language=language,
                        on_segment=self._progress_callback(session_id),
                    )
        except Exception as exc:
            self._handle_job_failure(metadata, exc)
            return

        single_source = self._single_known_source(metadata)
        segments = result.segments
        mic_speaker: Speaker | None = None
        if single_source is not None:
            segments = [seg.model_copy(update={"source": single_source}) for seg in segments]
            if single_source is AudioSource.MICROPHONE:
                # Same unconditional rule as the source-separated pipeline (Part C):
                # a lone microphone track is always "You", never run through diarization.
                segments, mic_speaker = assign_user_speaker(segments)

        transcript = Transcript(
            session_id=session_id,
            language=result.language,
            language_probability=result.language_probability,
            model_name=engine.model_name,
            segments=segments,
            speakers=[mic_speaker] if mic_speaker is not None else [],
        )
        self._finish_success(metadata, transcript)

    @staticmethod
    def _single_known_source(metadata: SessionMetadata) -> AudioSource | None:
        """The one audio source this mixed-pipeline transcript actually came from, when
        that's identifiable — exactly one audio track whose role is recognized as
        ``"system"`` or ``"microphone"``. ``None`` when the recording has zero tracks,
        more than one, or a track with an unrecognized role: in those cases
        ``AudioExtractor.extract_wav16k_mono()`` may have mixed multiple streams
        together (``amix``), so attributing the result to a single source would be a
        fabricated attribution, not a verified one — segments stay ``source=None``."""
        tracks = metadata.audio_tracks
        if len(tracks) != 1:
            return None
        role = tracks[0].role
        if role == AudioSource.SYSTEM.value:
            return AudioSource.SYSTEM
        if role == AudioSource.MICROPHONE.value:
            return AudioSource.MICROPHONE
        return None

    def _run_source_separated(
        self,
        metadata: SessionMetadata,
        language: str | None,
        tracks: dict[AudioSource, AudioTrackInfo],
    ) -> None:
        session_id = metadata.session_id
        recording_path = self._store.recording_path(metadata)
        engine = self._get_engine()  # one model load, reused for every source
        results: dict[AudioSource, _SourceResult] = {}
        mic_speaker: Speaker | None = None

        # One governor slot for the whole job (both sources), not one per source — a
        # concurrent session's job must not be able to interleave between this
        # session's system- and microphone-source transcriptions.
        with (
            self._resource_governor.heavy_job(),
            tempfile.TemporaryDirectory(prefix=f"lmr-transcribe-{session_id}-") as tmp,
        ):
            for source in _SOURCE_ORDER:
                track = tracks[source]
                try:
                    wav_path = Path(tmp) / f"audio_16k_mono_{source.value}.wav"
                    self._extractor.extract_track_wav16k_mono(
                        recording_path, wav_path, stream_index=track.index
                    )
                    raw = engine.transcribe(
                        wav_path,
                        language=language,
                        on_segment=self._progress_callback(session_id),
                    )
                    segments = [
                        TranscriptSegment(
                            start_s=seg.start_s, end_s=seg.end_s, text=seg.text, source=source
                        )
                        for seg in raw.segments
                    ]
                    if source is AudioSource.MICROPHONE:
                        # Unconditional — never gated on whether diarization is enabled
                        # or ever runs (Part C: "never assign a remote speaker identity
                        # to the microphone track").
                        segments, mic_speaker = assign_user_speaker(segments)
                    results[source] = _SourceResult(
                        segments=segments,
                        language=raw.language,
                        language_probability=raw.language_probability,
                    )
                except Exception as exc:
                    message = str(exc)
                    log.error(
                        "source-separated transcription failed for one source",
                        extra={
                            "session_id": session_id,
                            "source": source.value,
                            "error_message": message,
                        },
                    )
                    results[source] = _SourceResult(error=message)

        succeeded = {source: r for source, r in results.items() if r.error is None}
        failed_messages = {source: r.error for source, r in results.items() if r.error is not None}

        if not succeeded:
            combined = "; ".join(
                f"{source.value}: {msg}" for source, msg in failed_messages.items()
            )
            self._handle_job_failure(metadata, RuntimeError(combined))
            return

        all_segments = sorted(
            (seg for r in succeeded.values() for seg in r.segments), key=lambda s: s.start_s
        )
        primary = succeeded.get(AudioSource.SYSTEM) or next(iter(succeeded.values()))
        transcript = Transcript(
            session_id=session_id,
            language=primary.language,
            language_probability=primary.language_probability,
            model_name=engine.model_name,
            segments=all_segments,
            source_errors={source: msg for source, msg in failed_messages.items() if msg},
            speakers=[mic_speaker] if mic_speaker is not None else [],
        )
        if failed_messages:
            self._finish_partial(metadata, transcript, message="; ".join(failed_messages.values()))
        else:
            self._finish_success(metadata, transcript)

    def _progress_callback(self, session_id: str) -> OnSegment:
        def on_segment(processed_s: float, total_s: float | None) -> None:
            self._safe_callback(
                self._callbacks.on_progress,
                TranscriptionProgress(
                    session_id=session_id, processed_s=processed_s, total_s=total_s
                ),
            )

        return on_segment

    @staticmethod
    def _safe_callback(callback: Callable[..., None] | None, *args: object) -> None:
        """Invoke a UI-facing callback, if any. A callback that raises (e.g. a Qt signal
        source that was already torn down because the app quit mid-job) must not crash
        this background thread or be mistaken for a transcription failure."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("transcription callback failed")

    def _get_engine(self) -> TranscriptionProviderClient:
        if self._engine is None:
            self._engine = self._engine_factory()
        return self._engine

    def _get_diarization_engine(self) -> SpeakerDiarizationEngine:
        if self._diarization_engine_factory is None:
            raise DiarizationUnavailableError("diarization is not configured for this manager")
        if self._diarization_engine is None:
            self._diarization_engine = self._diarization_engine_factory()
        return self._diarization_engine

    def _run_diarization(self, metadata: SessionMetadata) -> None:
        session_id = metadata.session_id
        metadata.mark_diarization_running()
        self._write_quietly(metadata)
        self._safe_callback(self._callbacks.on_diarization_started, session_id)

        try:
            computation = self._compute_diarization(metadata)
        except Exception as exc:
            self._fail_diarization(metadata, exc, "diarization failed")
            with self._lock:
                self._diarizing.discard(session_id)
            return

        try:
            self._merge_and_persist_diarization(metadata, computation)
        except Exception as exc:
            self._fail_diarization(metadata, exc, "failed to persist a diarized transcript")
        finally:
            with self._lock:
                self._diarizing.discard(session_id)

    def _compute_diarization(self, metadata: SessionMetadata) -> _DiarizationComputation:
        """The slow, non-mutating phase: read the transcript, pick the system track,
        extract its audio, and run the diarization pipeline. Raises ``DiarizationError``
        (or a subclass) on any failure — nothing here has written anything yet."""
        session_id = metadata.session_id
        session_dir = self._store.session_dir(session_id)
        try:
            transcript = self._transcript_store.read(session_dir)
        except (OSError, ValueError) as exc:
            raise DiarizationError(
                f"no transcript to diarize yet for session {session_id}"
            ) from exc

        tracks = self._tracks_by_source(metadata)
        system_track = tracks.get(AudioSource.SYSTEM)
        if system_track is None:
            raise DiarizationError("this recording has no system audio track to diarize")

        system_positions = [
            i for i, s in enumerate(transcript.segments) if s.source is AudioSource.SYSTEM
        ]
        system_segments = [transcript.segments[i] for i in system_positions]
        if not system_segments:
            # Never report a successful diarization that tagged zero segments — a
            # recording whose system track wasn't attributed to any segment (e.g.
            # genuinely mixed audio with an unresolved role) has nothing eligible to
            # diarize, so this must fail cleanly rather than silently no-op as "done".
            raise DiarizationError(
                "the transcript has no system-audio segments to diarize (the "
                "recording's audio may have been captured as mixed audio rather "
                "than kept as separate, identifiable tracks)"
            )

        engine = self._get_diarization_engine()
        recording_path = self._store.recording_path(metadata)
        with (
            self._resource_governor.heavy_job(),
            tempfile.TemporaryDirectory(prefix=f"lmr-diarize-{session_id}-") as tmp,
        ):
            wav_path = Path(tmp) / "audio_16k_mono_system.wav"
            self._extractor.extract_track_wav16k_mono(
                recording_path, wav_path, stream_index=system_track.index
            )
            result = engine.diarize(wav_path)

        tagged_system, system_speakers = assign_system_speakers(system_segments, result)
        return _DiarizationComputation(
            session_dir=session_dir,
            base_revision=transcript.revision,
            system_positions=system_positions,
            tagged_system=tagged_system,
            system_speakers=system_speakers,
            result=result,
        )

    def _merge_and_persist_diarization(
        self, metadata: SessionMetadata, computation: _DiarizationComputation
    ) -> None:
        """The fast, lock-guarded phase: re-read the transcript fresh and only persist
        if nothing else wrote a newer revision while the pipeline above was running —
        see the module docstring's "write-race safety" section."""
        session_id = metadata.session_id
        session_dir = computation.session_dir
        with self._session_lock(session_id):
            current = self._transcript_store.read(session_dir)
            if current.revision != computation.base_revision:
                raise DiarizationError(
                    "the transcript changed while diarization was running (a newer "
                    "transcription finished in the meantime) — retry diarization to "
                    "apply it to the latest transcript"
                )
            new_segments = list(current.segments)
            for position, tagged_segment in zip(
                computation.system_positions, computation.tagged_system, strict=True
            ):
                new_segments[position] = tagged_segment
            non_system_speakers = [
                speaker for speaker in current.speakers if speaker.source_type != AudioSource.SYSTEM
            ]
            updated_transcript = current.model_copy(
                update={
                    "segments": new_segments,
                    "speakers": non_system_speakers + computation.system_speakers,
                    "diarization_model": computation.result.model_name,
                }
            )
            self._transcript_store.write(session_dir, updated_transcript)

        has_unknown_speaker = any(
            seg.speaker_id == UNKNOWN_SPEAKER_ID for seg in computation.tagged_system
        )
        metadata.mark_diarization_done(
            model=computation.result.model_name, has_unknown_speaker=has_unknown_speaker
        )
        self._write_quietly(metadata)
        self._safe_callback(
            self._callbacks.on_diarization_completed, session_id, updated_transcript
        )
        log.info(
            "diarization completed",
            extra={"session_id": session_id, "speaker_count": computation.result.speaker_count},
        )

    def _fail_diarization(
        self, metadata: SessionMetadata, exc: Exception, log_message: str
    ) -> None:
        session_id = metadata.session_id
        code = getattr(exc, "code", "diarization_failed")
        message = str(exc)
        metadata.mark_diarization_failed(message=message)
        self._write_quietly(metadata)
        log.error(
            log_message,
            extra={"session_id": session_id, "code": code, "error_message": message},
        )
        self._safe_callback(self._callbacks.on_diarization_failed, session_id, code, message)

    def _handle_job_failure(self, metadata: SessionMetadata, exc: Exception) -> None:
        session_id = metadata.session_id
        code = getattr(exc, "code", "transcription_failed")
        message = str(exc)
        self._mark_failed(metadata, message=message)
        log.error(
            "transcription failed",
            extra={"session_id": session_id, "code": code, "error_message": message},
        )
        self._safe_callback(self._callbacks.on_failed, session_id, code, message)

    def _write_new_transcript(self, session_id: str, transcript: Transcript) -> Transcript:
        """Assigns the next revision number and writes it under the per-session lock —
        the same lock ``_run_diarization`` uses to detect a stale snapshot, so a
        transcription write and a concurrent diarization write can never interleave."""
        session_dir = self._store.session_dir(session_id)
        with self._session_lock(session_id):
            try:
                current_revision = self._transcript_store.read(session_dir).revision
            except (OSError, ValueError):
                current_revision = -1
            versioned = transcript.model_copy(update={"revision": current_revision + 1})
            self._transcript_store.write(session_dir, versioned)
        return versioned

    def _finish_success(self, metadata: SessionMetadata, transcript: Transcript) -> None:
        # The transcription itself already succeeded by this point: a failure to persist
        # or to notify from here on must never be reported as a transcription failure.
        session_id = metadata.session_id
        try:
            versioned = self._write_new_transcript(session_id, transcript)
            self._mark_done(
                metadata,
                transcript_file=TRANSCRIPT_JSON_NAME,
                transcript=versioned,
            )
            self._safe_callback(self._callbacks.on_completed, session_id, versioned)
            log.info(
                "transcription completed",
                extra={"session_id": session_id, "language": versioned.language},
            )
        except Exception:
            log.exception(
                "failed to persist a completed transcript", extra={"session_id": session_id}
            )

    def _finish_partial(
        self, metadata: SessionMetadata, transcript: Transcript, *, message: str
    ) -> None:
        session_id = metadata.session_id
        try:
            versioned = self._write_new_transcript(session_id, transcript)
            metadata.mark_transcription_partial(
                transcript_file=TRANSCRIPT_JSON_NAME,
                language=versioned.language,
                model=versioned.model_name,
                message=message,
            )
            self._write_quietly(metadata)
            self._safe_callback(self._callbacks.on_completed, session_id, versioned)
            log.warning(
                "transcription partially completed",
                extra={
                    "session_id": session_id,
                    "failed_sources": [s.value for s in versioned.source_errors],
                },
            )
        except Exception:
            log.exception(
                "failed to persist a partial transcript", extra={"session_id": session_id}
            )

    def _mark_running(self, metadata: SessionMetadata) -> None:
        metadata.mark_transcription_running()
        self._write_quietly(metadata)

    def _mark_done(
        self, metadata: SessionMetadata, *, transcript_file: str, transcript: Transcript
    ) -> None:
        metadata.mark_transcription_done(
            transcript_file=transcript_file,
            language=transcript.language,
            model=transcript.model_name,
        )
        self._write_quietly(metadata)

    def _mark_failed(self, metadata: SessionMetadata, *, message: str) -> None:
        metadata.mark_transcription_failed(message=message)
        self._write_quietly(metadata)

    def _write_quietly(self, metadata: SessionMetadata) -> None:
        """Persist ``session.json``. A write failure here must never crash the job or
        touch the recording itself — it only means the UI may show stale status until the
        next successful write."""
        try:
            self._store.write(metadata)
        except OSError:
            log.exception(
                "failed to persist transcription status", extra={"session_id": metadata.session_id}
            )
