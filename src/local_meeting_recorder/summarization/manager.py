"""Runs AI meeting summary generation as a background job, one per session.

Wired in from ``ui/app_controller.py`` after transcription completes (see
``AppController._maybe_start_summary``) — completely independent of diarization:
summary generation never waits for speaker identification, it only needs a transcript to
already exist. A summary failure never touches the transcript or the recording; see
``domain/session_metadata.py``'s ``mark_summary_*`` methods, which only ever touch
``post_processing``.

Same background-job shape as ``transcription.manager.TranscriptionManager``'s
diarization half: a ``threading.Lock``-guarded ``set`` for duplicate-prevention, a
``_safe_callback`` wrapper so a torn-down Qt signal never crashes this thread, and a
retryable job that can be re-run independently at any time.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from local_meeting_recorder.domain.errors import SummaryError
from local_meeting_recorder.domain.session_metadata import SessionMetadata
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.summarization.markdown import render_summary_markdown
from local_meeting_recorder.summarization.models import MeetingSummary
from local_meeting_recorder.summarization.protocol import SummaryProviderClient
from local_meeting_recorder.summarization.store import SummaryStore
from local_meeting_recorder.transcription.store import TranscriptStore

log = get_logger(__name__)

ClientFactory = Callable[[], SummaryProviderClient]


@dataclass
class SummaryCallbacks:
    """UI-facing hooks. Plain callables — no Qt, same shape as ``TranscriptionCallbacks``."""

    on_started: Callable[[str], None] | None = None
    on_completed: Callable[[str, MeetingSummary], None] | None = None
    on_failed: Callable[[str, str, str], None] | None = None  # session_id, code, message


class SummaryManager:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        callbacks: SummaryCallbacks | None = None,
        client_factory: ClientFactory | None = None,
        transcript_store: TranscriptStore | None = None,
        summary_store: SummaryStore | None = None,
        resource_governor: ResourceGovernor | None = None,
    ) -> None:
        self._store = session_store
        self._callbacks = callbacks or SummaryCallbacks()
        self._client_factory = client_factory
        self._transcript_store = transcript_store or TranscriptStore()
        self._summary_store = summary_store or SummaryStore()
        # Shares one app-wide governor with TranscriptionManager when app.py wires them
        # together — unlimited unless the active processing profile says otherwise. See
        # infrastructure/resource_governor.py.
        self._resource_governor = resource_governor or ResourceGovernor()
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._running

    def generate_async(self, metadata: SessionMetadata) -> bool:
        """Start a background summary job for an already-transcribed session. Returns
        ``False`` without doing anything if a job for this session is already running —
        duplicate-prevention, same convention as ``TranscriptionManager``. Safe to call
        again any time after a failure or a success: each call appends one more version
        (see ``SummaryStore.append_version``), never overwriting an earlier summary."""
        with self._lock:
            if metadata.session_id in self._running:
                return False
            self._running.add(metadata.session_id)
        thread = threading.Thread(
            target=self._run,
            args=(metadata,),
            name=f"lmr-summarize-{metadata.session_id}",
            daemon=True,
        )
        thread.start()
        return True

    # -- internals ------------------------------------------------------------------------

    def _run(self, metadata: SessionMetadata) -> None:
        session_id = metadata.session_id
        try:
            metadata.mark_summary_running()
            self._write_quietly(metadata)
            self._safe_callback(self._callbacks.on_started, session_id)

            session_dir = self._store.session_dir(session_id)
            try:
                transcript = self._transcript_store.read(session_dir)
            except (OSError, ValueError) as exc:
                raise SummaryError(
                    f"no transcript to summarize yet for session {session_id}"
                ) from exc
            if not transcript.text.strip():
                raise SummaryError("the transcript has no speech to summarize")
            # Segment-indexed, not the plain human-readable rendering: lets the model
            # cite *which* transcript line(s) support a decision/action item via
            # "segment_refs" (Phase 5F) using the same positions `transcript.segments`
            # itself uses, rather than inventing its own reference scheme.
            text = transcript.text_with_segment_indices

            client = self._get_client()
            with self._resource_governor.heavy_job():
                summary = client.generate_summary(text)
        except Exception as exc:
            code = getattr(exc, "code", "summary_failed")
            message = str(exc)
            metadata.mark_summary_failed(message=message)
            self._write_quietly(metadata)
            log.error(
                "summary generation failed",
                extra={"session_id": session_id, "code": code, "error_message": message},
            )
            self._safe_callback(self._callbacks.on_failed, session_id, code, message)
            with self._lock:
                self._running.discard(session_id)
            return

        try:
            self._summary_store.append_version(session_dir, session_id=session_id, summary=summary)
            try:
                markdown = render_summary_markdown(
                    summary, metadata=metadata, transcript=transcript
                )
                self._summary_store.write_markdown(session_dir, markdown)
            except Exception:
                # The JSON summary (the version history of record) already succeeded —
                # a Markdown-rendering problem is a bonus-artifact failure, not a reason
                # to report the whole job as failed or skip the completed callback.
                log.exception(
                    "failed to render/persist summary.md", extra={"session_id": session_id}
                )
            metadata.mark_summary_done(provider=summary.provider, model=summary.model_name)
            self._write_quietly(metadata)
            self._safe_callback(self._callbacks.on_completed, session_id, summary)
            log.info("summary completed", extra={"session_id": session_id})
        except Exception:
            log.exception("failed to persist a completed summary", extra={"session_id": session_id})
        finally:
            with self._lock:
                self._running.discard(session_id)

    def _get_client(self) -> SummaryProviderClient:
        if self._client_factory is None:
            raise SummaryError("summary generation is not configured for this manager")
        return self._client_factory()

    @staticmethod
    def _safe_callback(callback: Callable[..., None] | None, *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("summary callback failed")

    def _write_quietly(self, metadata: SessionMetadata) -> None:
        try:
            self._store.write(metadata)
        except OSError:
            log.exception(
                "failed to persist summary status", extra={"session_id": metadata.session_id}
            )
