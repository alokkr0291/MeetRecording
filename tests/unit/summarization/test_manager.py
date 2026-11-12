"""``SummaryManager``: background job lifecycle, duplicate-prevention, failure isolation,
and that a retry appends a new version rather than erasing the one before it."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import local_meeting_recorder.summarization.manager as manager_module
from local_meeting_recorder.domain.errors import SummaryError
from local_meeting_recorder.domain.session_metadata import SessionMetadata, SummaryStatus
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore
from local_meeting_recorder.summarization.manager import SummaryCallbacks, SummaryManager
from local_meeting_recorder.summarization.models import ActionItem, Decision, MeetingSummary
from local_meeting_recorder.summarization.store import SummaryStore
from local_meeting_recorder.transcription.models import Transcript, TranscriptSegment
from local_meeting_recorder.transcription.store import TranscriptStore


class _FakeClient:
    def __init__(
        self, *, summary: MeetingSummary | None = None, error: Exception | None = None
    ) -> None:
        self.model_name = "fake-model"
        self._summary = summary
        self._error = error
        self.calls: list[str] = []

    def generate_summary(self, transcript_text: str) -> MeetingSummary:
        self.calls.append(transcript_text)
        if self._error is not None:
            raise self._error
        assert self._summary is not None
        return self._summary


def _build(
    tmp_path: Path, *, client: object = None
) -> tuple[SummaryManager, SessionStore, SessionMetadata]:
    paths = StoragePaths(root=tmp_path)
    paths.ensure()
    store = SessionStore(paths)
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)
    TranscriptStore().write(
        store.session_dir(metadata.session_id),
        Transcript(
            session_id=metadata.session_id,
            language="en",
            model_name="small",
            segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="hello everyone")],
        ),
    )
    manager = SummaryManager(
        session_store=store,
        client_factory=(lambda: client) if client is not None else None,
    )
    return manager, store, metadata


def _wait_until(predicate: object, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.01)
    return False


def test_successful_summary_is_persisted_and_marked_done(tmp_path: Path) -> None:
    summary = MeetingSummary(
        title="Sync", executive_summary="Talked about things.", provider="ollama", model_name="x"
    )
    client = _FakeClient(summary=summary)
    manager, store, metadata = _build(tmp_path, client=client)

    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    saved = store.read(metadata.session_id)
    assert saved.post_processing.summary_status is SummaryStatus.DONE
    assert saved.post_processing.summary_provider == "ollama"

    summary_set = SummaryStore().read(store.session_dir(metadata.session_id))
    assert summary_set is not None
    assert summary_set.latest is not None
    assert summary_set.latest.title == "Sync"
    # Segment-indexed, not the plain transcript, so the provider can cite "segment_refs".
    assert client.calls == ["[0] hello everyone"]


def test_successful_summary_writes_a_markdown_export(tmp_path: Path) -> None:
    summary = MeetingSummary(
        title="Weekly Sync",
        executive_summary="Talked about the roadmap.",
        topics=["roadmap"],
        decisions=[Decision(description="Ship beta Friday", segment_refs=[0])],
        action_items=[ActionItem(description="Write launch doc", owner="Alex", segment_refs=[0])],
        provider="ollama",
        model_name="llama3.1",
    )
    client = _FakeClient(summary=summary)
    manager, store, metadata = _build(tmp_path, client=client)

    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    markdown_path = store.session_dir(metadata.session_id) / "summary.md"
    assert markdown_path.is_file()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Weekly Sync" in markdown
    assert "ollama" in markdown and "llama3.1" in markdown
    assert "Talked about the roadmap." in markdown
    assert "Ship beta Friday" in markdown
    assert "Write launch doc" in markdown
    assert "Alex" in markdown
    assert "00:00" in markdown  # resolved timestamp for segment_refs=[0]


def test_markdown_export_failure_does_not_prevent_summary_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A problem rendering/writing summary.md is a bonus-artifact failure — the JSON
    summary (the version history of record) already succeeded and must still be
    reported as done."""

    def boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("rendering exploded")

    monkeypatch.setattr(manager_module, "render_summary_markdown", boom)  # type: ignore[attr-defined]

    summary = MeetingSummary(title="t", executive_summary="e", provider="ollama", model_name="x")
    manager, store, metadata = _build(tmp_path, client=_FakeClient(summary=summary))

    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    saved = store.read(metadata.session_id)
    assert saved.post_processing.summary_status is SummaryStatus.DONE
    summary_set = SummaryStore().read(store.session_dir(metadata.session_id))
    assert summary_set is not None and summary_set.latest is not None
    assert not (store.session_dir(metadata.session_id) / "summary.md").exists()


def test_failure_marks_failed_and_never_touches_the_transcript(tmp_path: Path) -> None:
    client = _FakeClient(error=SummaryError("provider exploded"))
    manager, store, metadata = _build(tmp_path, client=client)

    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    saved = store.read(metadata.session_id)
    assert saved.post_processing.summary_status is SummaryStatus.FAILED
    assert saved.post_processing.summary_error == "provider exploded"

    transcript = TranscriptStore().read(store.session_dir(metadata.session_id))
    assert transcript.segments[0].text == "hello everyone"


def test_retry_after_failure_preserves_the_failed_version_count(tmp_path: Path) -> None:
    client = _FakeClient(error=SummaryError("boom"))
    manager, store, metadata = _build(tmp_path, client=client)
    manager.generate_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    summary = MeetingSummary(
        title="Retry worked", executive_summary="ok", provider="ollama", model_name="x"
    )
    client._error = None  # type: ignore[attr-defined]
    client._summary = summary  # type: ignore[attr-defined]
    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))

    saved = store.read(metadata.session_id)
    assert saved.post_processing.summary_status is SummaryStatus.DONE
    summary_set = SummaryStore().read(store.session_dir(metadata.session_id))
    assert summary_set is not None
    assert len(summary_set.versions) == 1  # the failed attempt never wrote a version
    assert summary_set.latest is not None
    assert summary_set.latest.title == "Retry worked"


def test_duplicate_job_for_the_same_session_is_rejected(tmp_path: Path) -> None:
    summary = MeetingSummary(title="t", executive_summary="e", provider="ollama", model_name="x")
    manager, _store, metadata = _build(tmp_path, client=_FakeClient(summary=summary))
    assert manager.generate_async(metadata) is True
    assert manager.generate_async(metadata) is False
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))


def test_without_a_client_factory_fails_cleanly(tmp_path: Path) -> None:
    manager, store, metadata = _build(tmp_path)
    assert manager.generate_async(metadata) is True
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    saved = store.read(metadata.session_id)
    assert saved.post_processing.summary_status is SummaryStatus.FAILED


def test_callbacks_fire_on_success(tmp_path: Path) -> None:
    summary = MeetingSummary(title="t", executive_summary="e", provider="ollama", model_name="x")
    started: list[str] = []
    completed: list[tuple[str, MeetingSummary]] = []
    manager = None

    def on_started(session_id: str) -> None:
        started.append(session_id)

    def on_completed(session_id: str, result: MeetingSummary) -> None:
        completed.append((session_id, result))

    paths = StoragePaths(root=tmp_path)
    paths.ensure()
    store = SessionStore(paths)
    metadata = SessionMetadata(session_id="session-1")
    store.write(metadata)
    TranscriptStore().write(
        store.session_dir(metadata.session_id),
        Transcript(
            session_id=metadata.session_id,
            language="en",
            model_name="small",
            segments=[TranscriptSegment(start_s=0.0, end_s=1.0, text="hi")],
        ),
    )
    manager = SummaryManager(
        session_store=store,
        callbacks=SummaryCallbacks(on_started=on_started, on_completed=on_completed),
        client_factory=lambda: _FakeClient(summary=summary),
    )
    manager.generate_async(metadata)
    assert _wait_until(lambda: not manager.is_running(metadata.session_id))
    assert started == [metadata.session_id]
    assert completed == [(metadata.session_id, summary)]
