"""Caps how many heavy AI jobs — Whisper transcription, pyannote diarization, summary
generation — run at once across the whole application, not per-session (Phase 6).

``TranscriptionManager`` already prevents two jobs for the *same* session from running
concurrently, but nothing previously stopped two *different* sessions' jobs (e.g. two
recordings saved back to back) from each loading a model and running inference at the
same time — on an entry-level CPU with limited RAM, that is exactly the situation the
"Low Resource" processing profile exists to avoid.

Recording itself never goes through this — only post-processing jobs do — so a
recording in progress is never blocked waiting for a turn here, regardless of profile.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: 0 means "no limit" — every job proceeds immediately, identical to the behavior before
#: this module existed. This is the default so nothing changes unless a caller opts in.
UNLIMITED = 0


class ResourceGovernor:
    """A single shared counter guarded by one condition variable, not a
    ``threading.Semaphore`` — a semaphore's internal count can't be resized safely
    while jobs already hold permits against it, and the active processing profile can
    change the limit at any time (``set_limit``) while jobs are in flight."""

    def __init__(self, *, max_concurrent_ai_jobs: int = UNLIMITED) -> None:
        self._condition = threading.Condition()
        self._limit = max_concurrent_ai_jobs
        self._active = 0

    def set_limit(self, max_concurrent_ai_jobs: int) -> None:
        """Takes effect immediately, including for jobs already waiting — a job
        in-flight (already past the wait) is never pre-empted, only new/waiting
        acquisitions see the new limit."""
        with self._condition:
            self._limit = max_concurrent_ai_jobs
            self._condition.notify_all()

    @property
    def limit(self) -> int:
        with self._condition:
            return self._limit

    @contextmanager
    def heavy_job(self) -> Iterator[None]:
        """Blocks until a slot is free (or returns immediately when unlimited), then
        frees it on the way out — including on an exception, so a crashed job never
        permanently starves every job after it."""
        with self._condition:
            while self._limit > 0 and self._active >= self._limit:
                self._condition.wait()
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
