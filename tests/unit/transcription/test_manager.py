"""TranscriptionManager: background job execution, duplicate prevention, retry, and the
guarantee that a transcription failure never touches the session's own ``status``.

Uses a fake extractor + fake engine (no real ffmpeg, no real faster-whisper) — the same
"fake the external boundary" style as ``capture/fake.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.errors import AudioExtractionError, DiarizationError
from local_meeting_recorder.domain.session_metadata import (
    AudioTrackInfo,
    DiarizationStatus,
    SessionMetadata,
    SessionStatus,
    TranscriptionStatus,
)
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.speaker.protocol import DiarizationResult, SpeakerRegion
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.transcription.engine import EngineResult
from local_meeting_recorder.transcription.manager import (
    TranscriptionCallbacks,
    TranscriptionManager,
    TranscriptionProgress,
)
from local_meeting_recorder.transcription.models import (
    USER_SPEAKER_ID,
    AudioSource,
    TranscriptSegment,
)
from local_meeting_recorder.transcription.store import TranscriptStore


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _FakeExtractor:
    def __init__(
        self, *, fail_times: int = 0, fail_at_stream_index: set[int] | None = None
    ) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self.track_calls: list[tuple[Path, Path, int]] = []
        self._remaining_failures = fail_times
        self._fail_at_stream_index = fail_at_stream_index or set()

    def extract_wav16k_mono(self, source: Path, dest: Path) -> Path:
        self.calls.append((source, dest))
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AudioExtractionError("simulated ffmpeg failure")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-wav-bytes")
        return dest

    def extract_track_wav16k_mono(self, source: Path, dest: Path, *, stream_index: int) -> Path:
        self.track_calls.append((source, dest, stream_index))
        if stream_index in self._fail_at_stream_index:
            raise AudioExtractionError(f"simulated ffmpeg failure for stream {stream_index}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-wav-bytes")
        return dest


class _FakeEngine:
    """``responses`` (if given) is consumed one entry per ``transcribe()`` call, in
    order — an ``Exception`` instance raises instead of returning. ``None`` (the
    default) always returns one canned, successful result — enough for every test that
    only ever calls ``transcribe()`` once."""

    def __init__(
        self,
        *,
        model_size: str = "tiny",
        responses: list[EngineResult | Exception] | None = None,
    ) -> None:
        self.model_size = model_size
        self.model_name = model_size
        self.calls: list[Path] = []
        self.block = threading.Event()
        self.block.set()  # unblocked by default
        self._responses = responses
        self._call_count = 0

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        on_segment: Callable[[float, float | None], None] | None = None,
    ) -> EngineResult:
        self.calls.append(audio_path)
        self.block.wait(timeout=5.0)
        if self._responses is not None:
            response = self._responses[self._call_count]
            self._call_count += 1
            if isinstance(response, Exception):
                raise response
            result = response
        else:
            result = EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=2.0,
                segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="hello")],
            )
        if on_segment is not None:
            for seg in result.segments:
                on_segment(seg.end_s, result.duration_s)
        return result


class _FakeDiarizationEngine:
    def __init__(
        self, *, result: DiarizationResult | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[Path] = []
        self.block = threading.Event()
        self.block.set()  # unblocked by default — same convention as `_FakeEngine.block`

    def diarize(self, audio_path: Path) -> DiarizationResult:
        self.calls.append(audio_path)
        self.block.wait(timeout=5.0)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _build_manager(
    tmp_path: Path,
    *,
    extractor: _FakeExtractor,
    engine: _FakeEngine,
    callbacks: TranscriptionCallbacks | None = None,
    diarization_engine: object = None,
    resource_governor: ResourceGovernor | None = None,
) -> tuple[TranscriptionManager, SessionStore]:
    paths = StoragePaths(root=tmp_path)
    paths.ensure()
    store = SessionStore(paths)
    settings = Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]
    manager = TranscriptionManager(
        session_store=store,
        settings=settings,
        callbacks=callbacks,
        engine_factory=lambda: engine,  # type: ignore[arg-type]
        audio_extractor=extractor,  # type: ignore[arg-type]
        transcript_store=TranscriptStore(),
        diarization_engine_factory=(lambda: diarization_engine)  # type: ignore[arg-type]
        if diarization_engine is not None
        else None,
        resource_governor=resource_governor,
    )
    return manager, store


def _saved_metadata(session_id: str = "lmr_20261110T000000_aaaa") -> SessionMetadata:
    metadata = SessionMetadata(session_id=session_id)
    metadata.status = SessionStatus.SAVED
    return metadata


def _saved_metadata_with_both_tracks(
    session_id: str = "lmr_20261110T000000_bbbb",
) -> SessionMetadata:
    """Matches a real recording's track layout (verified against actual hardware
    capture — see IMPLEMENTATION_STATUS.md's Phase 5 "Part A"): index 1 = system,
    index 2 = microphone."""
    metadata = _saved_metadata(session_id)
    metadata.audio_tracks = [
        AudioTrackInfo(index=1, role="system", codec="aac"),
        AudioTrackInfo(index=2, role="microphone", codec="aac"),
    ]
    return metadata


def test_successful_job_writes_transcript_and_marks_done(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()

    assert manager.transcribe_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    assert TranscriptStore().exists(store.session_dir(metadata.session_id))
    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.transcript_status is TranscriptionStatus.DONE
    assert persisted.post_processing.transcript_language == "en"
    assert persisted.post_processing.transcript_file == "transcript.json"
    assert persisted.status is SessionStatus.SAVED  # untouched


def test_duplicate_job_for_same_session_is_rejected(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    engine.block.clear()  # first job blocks until we release it
    manager, _store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()

    assert manager.transcribe_async(metadata) is True
    assert _wait_until(lambda: manager.is_running(metadata.session_id))
    assert manager.transcribe_async(metadata) is False  # rejected: already running

    engine.block.set()
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(extractor.calls) == 1  # the rejected call never ran extraction again


# -- resource governor: Low Resource profile serializes jobs across sessions -----------


def test_resource_governor_serializes_two_different_sessions(tmp_path: Path) -> None:
    """The per-session ``_running`` set only prevents a session's job from racing
    itself — a shared ``ResourceGovernor(max_concurrent_ai_jobs=1)`` is what stops two
    *different* sessions' jobs from running at once, which is what the "Low Resource"
    processing profile needs."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    engine.block.clear()  # the first job blocks mid-transcribe until released below
    governor = ResourceGovernor(max_concurrent_ai_jobs=1)
    manager, _store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, resource_governor=governor
    )
    metadata_a = _saved_metadata(session_id="lmr_20261110T000000_aaaa")
    metadata_b = _saved_metadata(session_id="lmr_20261110T000001_bbbb")

    assert manager.transcribe_async(metadata_a) is True
    assert _wait_until(lambda: len(engine.calls) == 1)  # A is now inside transcribe()

    assert manager.transcribe_async(metadata_b) is True
    time.sleep(0.05)
    assert len(engine.calls) == 1  # B must wait — the one governor slot is held by A

    engine.block.set()  # release A; B can now proceed
    assert _wait_until(lambda: len(engine.calls) == 2)
    assert _wait_until(lambda: not manager.is_running(metadata_a.session_id))
    assert _wait_until(lambda: not manager.is_running(metadata_b.session_id))


def test_without_a_governor_two_sessions_run_concurrently(tmp_path: Path) -> None:
    """Sanity check: the default (no governor passed) must be unchanged from
    pre-Phase-6 behavior — unrelated sessions' jobs were never serialized before, and
    still aren't unless a caller opts in."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    engine.block.clear()
    manager, _store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata_a = _saved_metadata(session_id="lmr_20261110T000000_aaaa")
    metadata_b = _saved_metadata(session_id="lmr_20261110T000001_bbbb")

    manager.transcribe_async(metadata_a)
    assert _wait_until(lambda: len(engine.calls) == 1)

    manager.transcribe_async(metadata_b)
    assert _wait_until(lambda: len(engine.calls) == 2)  # B starts without waiting for A

    engine.block.set()
    assert _wait_until(lambda: not manager.is_running(metadata_a.session_id))
    assert _wait_until(lambda: not manager.is_running(metadata_b.session_id))


def test_retry_after_failure_succeeds_and_recording_status_stays_saved(tmp_path: Path) -> None:
    extractor = _FakeExtractor(fail_times=1)
    engine = _FakeEngine()
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()

    assert manager.transcribe_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    after_failure = store.read(metadata.session_id)
    assert after_failure.post_processing.transcript_status is TranscriptionStatus.FAILED
    assert after_failure.post_processing.transcript_error
    assert after_failure.status is SessionStatus.SAVED  # failure never touches this

    assert manager.transcribe_async(after_failure) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    after_retry = store.read(metadata.session_id)
    assert after_retry.post_processing.transcript_status is TranscriptionStatus.DONE
    assert after_retry.status is SessionStatus.SAVED


def test_callbacks_fire_in_order(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    events: list[str] = []
    callbacks = TranscriptionCallbacks(
        on_started=lambda session_id: events.append(f"started:{session_id}"),
        on_progress=lambda progress: events.append(f"progress:{progress.processed_s}"),
        on_completed=lambda session_id, transcript: events.append(f"completed:{session_id}"),
        on_failed=lambda session_id, code, message: events.append(f"failed:{session_id}:{code}"),
    )
    manager, _store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, callbacks=callbacks
    )
    metadata = _saved_metadata()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert events == [
        "started:lmr_20261110T000000_aaaa",
        "progress:1.0",
        "completed:lmr_20261110T000000_aaaa",
    ]


def test_on_failed_callback_fires_with_code_and_message(tmp_path: Path) -> None:
    extractor = _FakeExtractor(fail_times=99)
    engine = _FakeEngine()
    failures: list[tuple[str, str, str]] = []
    callbacks = TranscriptionCallbacks(on_failed=lambda *args: failures.append(args))
    manager, _store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, callbacks=callbacks
    )
    metadata = _saved_metadata()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(failures) == 1
    session_id, code, message = failures[0]
    assert session_id == metadata.session_id
    assert code == "audio_extraction_failed"
    assert "simulated ffmpeg failure" in message


def test_progress_reports_processed_and_total_seconds(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    progresses: list[TranscriptionProgress] = []
    callbacks = TranscriptionCallbacks(on_progress=progresses.append)
    manager, _store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, callbacks=callbacks
    )
    metadata = _saved_metadata()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(progresses) == 1
    assert progresses[0].processed_s == 1.0
    assert progresses[0].total_s == 2.0


# -- source-separated transcription (Phase 5) ----------------------------------------------


def test_falls_back_to_mixed_pipeline_with_no_track_metadata(tmp_path: Path) -> None:
    """A session with no ``audio_tracks`` at all (legacy, or a Phase 4 session) must use
    the original single-pass mixdown, not the source-separated path."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, _store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()  # no audio_tracks

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(extractor.calls) == 1  # extract_wav16k_mono (mixdown), not per-track
    assert extractor.track_calls == []


def test_falls_back_to_mixed_pipeline_with_only_one_track(tmp_path: Path) -> None:
    """Microphone capture disabled for this recording: only a system track exists, so
    there is nothing to source-separate."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, _store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()
    metadata.audio_tracks = [AudioTrackInfo(index=1, role="system", codec="aac")]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(extractor.calls) == 1
    assert extractor.track_calls == []


def test_mixed_pipeline_single_system_track_tags_segments_as_system(tmp_path: Path) -> None:
    """The fix for the diarization no-op bug: a lone *system* track is the whole
    recording, so its segments must be attributed, not left ``source=None``."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()
    metadata.audio_tracks = [AudioTrackInfo(index=1, role="system", codec="aac")]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert transcript.segments
    assert all(s.source is AudioSource.SYSTEM for s in transcript.segments)
    assert transcript.speakers == []  # no diarization has run yet


def test_mixed_pipeline_single_microphone_track_assigns_user_speaker(tmp_path: Path) -> None:
    """A lone *microphone* track must still get the fixed "You" identity, unconditionally
    — the same Part C rule the source-separated pipeline already enforces."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()
    metadata.audio_tracks = [AudioTrackInfo(index=2, role="microphone", codec="aac")]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert transcript.segments
    assert all(s.source is AudioSource.MICROPHONE for s in transcript.segments)
    assert all(s.speaker_id == USER_SPEAKER_ID for s in transcript.segments)
    assert {s.speaker_id for s in transcript.speakers} == {USER_SPEAKER_ID}


def test_mixed_pipeline_with_an_unresolved_role_track_stays_unattributed(tmp_path: Path) -> None:
    """Two tracks, only one of recognizable role: must NOT fabricate a single-source
    attribution — this is genuinely ambiguous (the extractor may have mixed them)."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata()
    metadata.audio_tracks = [
        AudioTrackInfo(index=1, role="system", codec="aac"),
        AudioTrackInfo(index=3, role="screen-share-audio", codec="aac"),
    ]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert transcript.segments
    assert all(s.source is None for s in transcript.segments)


def test_diarization_succeeds_for_a_single_system_track_recording(tmp_path: Path) -> None:
    """Regression for the silent no-op bug: diarization must actually tag segments (and
    must not just report "done") when microphone capture was disabled."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(
            regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
            speaker_count=1,
            model_name="m",
        )
    )
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, diarization_engine=diarization_engine
    )
    metadata = _saved_metadata()
    metadata.audio_tracks = [AudioTrackInfo(index=1, role="system", codec="aac")]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    assert manager.diarize_async(metadata) is True
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.DONE
    diarized = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert diarized.segments[0].speaker_id == "speaker_1"
    assert {s.speaker_id for s in diarized.speakers} == {"speaker_1"}


def test_diarization_fails_cleanly_when_no_segments_are_eligible(tmp_path: Path) -> None:
    """A system track exists in the recording's metadata, but the mixed pipeline could
    not attribute any segment to it (ambiguous multi-track case) — diarization must fail
    with a clear reason rather than report success having tagged zero segments."""
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(regions=[], speaker_count=0, model_name="m")
    )
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, diarization_engine=diarization_engine
    )
    metadata = _saved_metadata()
    metadata.audio_tracks = [
        AudioTrackInfo(index=1, role="system", codec="aac"),
        AudioTrackInfo(index=3, role="screen-share-audio", codec="aac"),
    ]

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.FAILED
    assert "no system-audio segments" in (persisted.post_processing.diarization_error or "")
    assert diarization_engine.calls == []  # never ran the (expensive) pipeline at all


def test_source_separated_extracts_system_before_microphone(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine(
        responses=[
            EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=1.0,
                segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="system speech")],
            ),
            EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=1.0,
                segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="mic speech")],
            ),
        ]
    )
    manager, _store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert [c[2] for c in extractor.track_calls] == [1, 2]  # system (index 1) first


def test_source_separated_merges_segments_onto_one_timeline(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine(
        responses=[
            EngineResult(  # system
                language="en",
                language_probability=0.9,
                duration_s=3.0,
                segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="hello everyone")],
            ),
            EngineResult(  # microphone
                language="en",
                language_probability=0.85,
                duration_s=3.0,
                segments=[TranscriptSegment(start_s=0.5, end_s=1.5, text="thanks for joining")],
            ),
        ]
    )
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.transcript_status is TranscriptionStatus.DONE
    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert [(s.start_s, s.source, s.speaker_id, s.text) for s in transcript.segments] == [
        (0.0, AudioSource.SYSTEM, None, "hello everyone"),
        (0.5, AudioSource.MICROPHONE, USER_SPEAKER_ID, "thanks for joining"),
    ]
    assert transcript.source_errors == {}
    # The mic segment always gets the fixed "user" speaker identity (Part C), shown in
    # preference to the bare [Mic] source label; the system segment has no speaker_id
    # yet (diarization hasn't run), so it still falls back to [System].
    assert transcript.text == "[System] hello everyone\n[You] thanks for joining"


def test_source_separated_one_extraction_failure_produces_partial_result(tmp_path: Path) -> None:
    extractor = _FakeExtractor(fail_at_stream_index={2})  # microphone extraction fails
    engine = _FakeEngine()  # only called once, for the system source
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.transcript_status is TranscriptionStatus.PARTIAL
    assert persisted.status is SessionStatus.SAVED  # the recording itself is untouched
    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert len(transcript.segments) == 1
    assert transcript.segments[0].source is AudioSource.SYSTEM
    assert AudioSource.MICROPHONE in transcript.source_errors


def test_source_separated_one_transcription_failure_produces_partial_result(
    tmp_path: Path,
) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine(
        responses=[
            EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=1.0,
                segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="system ok")],
            ),
            RuntimeError("model crashed on this source"),
        ]
    )
    manager, store = _build_manager(tmp_path, extractor=extractor, engine=engine)
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.transcript_status is TranscriptionStatus.PARTIAL
    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert [s.source for s in transcript.segments] == [AudioSource.SYSTEM]
    assert "model crashed" in transcript.source_errors[AudioSource.MICROPHONE]


def test_source_separated_both_sources_failing_marks_job_failed(tmp_path: Path) -> None:
    extractor = _FakeExtractor(fail_at_stream_index={1, 2})
    engine = _FakeEngine()
    callbacks = TranscriptionCallbacks()
    failures: list[tuple[str, str, str]] = []
    callbacks.on_failed = lambda *args: failures.append(args)
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, callbacks=callbacks
    )
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.transcript_status is TranscriptionStatus.FAILED
    assert persisted.status is SessionStatus.SAVED
    assert not TranscriptStore().exists(store.session_dir(metadata.session_id))
    assert len(failures) == 1


def test_source_separated_reuses_the_same_engine_instance_for_both_sources(
    tmp_path: Path,
) -> None:
    """'Reuse the existing transcription engine wherever possible' — one model load,
    not one per source."""
    extractor = _FakeExtractor()
    engine = _FakeEngine(
        responses=[
            EngineResult(language="en", language_probability=0.9, duration_s=1.0, segments=[]),
            EngineResult(language="en", language_probability=0.9, duration_s=1.0, segments=[]),
        ]
    )
    engine_factory_calls: list[int] = []

    def engine_factory() -> _FakeEngine:
        engine_factory_calls.append(1)
        return engine

    paths = StoragePaths(root=tmp_path)
    paths.ensure()
    store = SessionStore(paths)
    settings = Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]
    manager = TranscriptionManager(
        session_store=store,
        settings=settings,
        engine_factory=engine_factory,  # type: ignore[arg-type]
        audio_extractor=extractor,  # type: ignore[arg-type]
        transcript_store=TranscriptStore(),
    )
    metadata = _saved_metadata_with_both_tracks()

    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert len(engine_factory_calls) == 1
    assert len(engine.calls) == 2


# -- diarization (Phase 5C) -----------------------------------------------------------------


def _transcribe_then_diarize(
    tmp_path: Path, *, diarization_engine: object
) -> tuple[TranscriptionManager, SessionStore, SessionMetadata]:
    """Run a real (fake-backed) source-separated transcription first, since diarization
    operates on an already-written transcript, then return the manager ready to diarize."""
    extractor = _FakeExtractor()
    engine = _FakeEngine(
        responses=[
            EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=4.0,
                segments=[
                    TranscriptSegment(
                        start_s=0.0, end_s=1.0, text="hello", source=AudioSource.SYSTEM
                    ),
                    TranscriptSegment(
                        start_s=2.0, end_s=3.0, text="world", source=AudioSource.SYSTEM
                    ),
                ],
            ),
            EngineResult(
                language="en",
                language_probability=0.9,
                duration_s=4.0,
                segments=[TranscriptSegment(start_s=1.0, end_s=2.0, text="mic speech")],
            ),
        ]
    )
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, diarization_engine=diarization_engine
    )
    metadata = _saved_metadata_with_both_tracks()
    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert store.read(metadata.session_id).post_processing.transcript_status is (
        TranscriptionStatus.DONE
    )
    return manager, store, metadata


def test_diarization_assigns_speaker_ids_to_system_segments_only(tmp_path: Path) -> None:
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(
            regions=[
                SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00"),
                SpeakerRegion(start_s=2.0, end_s=3.0, speaker_label="SPEAKER_01"),
            ],
            speaker_count=2,
            model_name="pyannote/speaker-diarization-3.1",
        )
    )
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )

    assert manager.diarize_async(metadata) is True
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.DONE
    assert persisted.post_processing.diarization_has_unknown_speaker is False
    assert persisted.status is SessionStatus.SAVED  # untouched

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    by_text = {s.text: s.speaker_id for s in transcript.segments}
    assert by_text["hello"] == "speaker_1"
    assert by_text["world"] == "speaker_2"
    assert by_text["mic speech"] == USER_SPEAKER_ID  # unaffected by diarization
    assert transcript.diarization_model == "pyannote/speaker-diarization-3.1"
    speaker_ids = {s.speaker_id for s in transcript.speakers}
    assert speaker_ids == {"speaker_1", "speaker_2", USER_SPEAKER_ID}


def test_diarization_flags_incomplete_attribution_when_a_segment_is_unmatched(
    tmp_path: Path,
) -> None:
    """One diarization region only covers the first system segment ("hello" at
    0.0-1.0); the second ("world" at 2.0-3.0) has no overlapping region at all, so it
    gets UNKNOWN_SPEAKER_ID — this must be reflected in
    ``diarization_has_unknown_speaker`` so the UI can say "partially identified"."""
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(
            regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
            speaker_count=1,
            model_name="m",
        )
    )
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )

    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.DONE
    assert persisted.post_processing.diarization_has_unknown_speaker is True


def test_diarization_never_touches_microphone_segments(tmp_path: Path) -> None:
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(regions=[], speaker_count=0, model_name="m")
    )
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )
    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    mic_segment = next(s for s in transcript.segments if s.text == "mic speech")
    assert mic_segment.speaker_id == USER_SPEAKER_ID


def test_diarization_failure_preserves_the_transcript(tmp_path: Path) -> None:
    diarization_engine = _FakeDiarizationEngine(error=DiarizationError("model crashed"))
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )
    before = TranscriptStore().read(store.session_dir(metadata.session_id))

    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.FAILED
    assert persisted.post_processing.diarization_error
    assert persisted.post_processing.transcript_status is TranscriptionStatus.DONE  # untouched
    assert persisted.status is SessionStatus.SAVED

    after = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert after == before  # the transcript file itself is byte-for-byte unchanged


def test_diarization_can_be_retried_after_failure(tmp_path: Path) -> None:
    diarization_engine = _FakeDiarizationEngine(error=DiarizationError("first attempt failed"))
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )
    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))
    assert (
        store.read(metadata.session_id).post_processing.diarization_status
        is DiarizationStatus.FAILED
    )

    diarization_engine.error = None
    diarization_engine.result = DiarizationResult(
        regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
        speaker_count=1,
        model_name="m",
    )
    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))
    assert (
        store.read(metadata.session_id).post_processing.diarization_status is DiarizationStatus.DONE
    )


def test_diarization_duplicate_job_is_rejected(tmp_path: Path) -> None:
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(regions=[], speaker_count=0, model_name="m")
    )
    manager, _store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )
    assert manager.diarize_async(metadata) is True
    assert manager.diarize_async(metadata) is False  # a diarization job is already running
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))


def test_diarization_without_a_system_track_fails_cleanly(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    engine = _FakeEngine()
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(regions=[], speaker_count=0, model_name="m")
    )
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, diarization_engine=diarization_engine
    )
    metadata = _saved_metadata()  # no audio_tracks at all -> mixed pipeline
    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))
    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.FAILED
    assert "system audio track" in (persisted.post_processing.diarization_error or "")


def test_diarization_without_an_engine_factory_fails_cleanly(tmp_path: Path) -> None:
    manager, store, metadata = _transcribe_then_diarize(tmp_path, diarization_engine=None)
    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))
    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.FAILED


# -- write-race safety (retry-transcription vs. in-flight diarization) ----------------------


def test_retry_transcription_during_diarization_does_not_corrupt_the_transcript(
    tmp_path: Path,
) -> None:
    """Reproduces the previously-identified interleaving: a user retries transcription
    while an old diarization job (started against the pre-retry transcript) is still
    running. The diarization job must detect that the transcript moved on and must NOT
    overwrite the newer transcription result with speaker tags computed against stale
    segments."""
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(
            regions=[
                SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00"),
                SpeakerRegion(start_s=2.0, end_s=3.0, speaker_label="SPEAKER_01"),
            ],
            speaker_count=2,
            model_name="m",
        )
    )
    diarization_engine.block.clear()  # hold diarization mid-flight until released below
    # Two full source-separated passes worth of canned responses: the initial
    # transcription (consumed by `_transcribe_then_diarize`) and the later retry.
    extractor = _FakeExtractor()
    system_result = EngineResult(
        language="en",
        language_probability=0.9,
        duration_s=4.0,
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="hello", source=AudioSource.SYSTEM),
            TranscriptSegment(start_s=2.0, end_s=3.0, text="world", source=AudioSource.SYSTEM),
        ],
    )
    mic_result = EngineResult(
        language="en",
        language_probability=0.9,
        duration_s=4.0,
        segments=[TranscriptSegment(start_s=1.0, end_s=2.0, text="mic speech")],
    )
    engine = _FakeEngine(responses=[system_result, mic_result, system_result, mic_result])
    manager, store = _build_manager(
        tmp_path, extractor=extractor, engine=engine, diarization_engine=diarization_engine
    )
    metadata = _saved_metadata_with_both_tracks()
    manager.transcribe_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert store.read(metadata.session_id).post_processing.transcript_status is (
        TranscriptionStatus.DONE
    )

    assert manager.diarize_async(metadata) is True
    assert _wait_until(lambda: manager.is_diarizing(metadata.session_id))
    assert _wait_until(lambda: len(diarization_engine.calls) == 1)  # now blocked inside diarize()

    # The retry completes in full *while* diarization is still blocked mid-pipeline.
    assert manager.transcribe_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    newer = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert newer.revision == 1  # a fresh write, distinct from the one diarization read

    diarization_engine.block.set()  # release diarization; it now tries to persist
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.FAILED
    assert "changed while diarization was running" in (
        persisted.post_processing.diarization_error or ""
    )
    assert persisted.post_processing.transcript_status is TranscriptionStatus.DONE  # untouched

    final = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert final == newer  # the newer transcription result was never clobbered
    assert all(s.speaker_id in (None, USER_SPEAKER_ID) for s in final.segments)


def test_diarization_succeeds_normally_when_nothing_races_it(tmp_path: Path) -> None:
    """Sanity check: the revision guard must not false-positive in the ordinary,
    non-racing case — this is effectively the pre-existing
    ``test_diarization_assigns_speaker_ids_to_system_segments_only`` behavior, re-asserted
    alongside the new revision bookkeeping."""
    diarization_engine = _FakeDiarizationEngine(
        result=DiarizationResult(
            regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
            speaker_count=1,
            model_name="m",
        )
    )
    manager, store, metadata = _transcribe_then_diarize(
        tmp_path, diarization_engine=diarization_engine
    )
    before = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert before.revision == 0

    manager.diarize_async(metadata)
    assert _wait_until(lambda: not manager.is_diarizing(metadata.session_id))

    persisted = store.read(metadata.session_id)
    assert persisted.post_processing.diarization_status is DiarizationStatus.DONE
