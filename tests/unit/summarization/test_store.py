"""``SummaryStore``: atomic read/write of ``summary.json``, and version preservation."""

from __future__ import annotations

import json
from pathlib import Path

from local_meeting_recorder.summarization.models import MeetingSummary, SummarySet
from local_meeting_recorder.summarization.store import SummaryStore


def _summary(
    *, provider: str = "ollama", model_name: str = "llama3.1", title: str = "Sync"
) -> MeetingSummary:
    return MeetingSummary(
        title=title, executive_summary="A summary.", provider=provider, model_name=model_name
    )


def test_read_with_no_file_returns_none(tmp_path: Path) -> None:
    assert SummaryStore().read(tmp_path) is None


def test_append_version_creates_the_first_version(tmp_path: Path) -> None:
    store = SummaryStore()
    summary_set = store.append_version(tmp_path, session_id="s1", summary=_summary())
    assert summary_set.session_id == "s1"
    assert len(summary_set.versions) == 1
    assert summary_set.latest is not None
    assert summary_set.latest.title == "Sync"


def test_append_version_preserves_earlier_versions(tmp_path: Path) -> None:
    store = SummaryStore()
    store.append_version(tmp_path, session_id="s1", summary=_summary(title="First"))
    second = store.append_version(
        tmp_path, session_id="s1", summary=_summary(provider="openai", title="Second")
    )
    assert len(second.versions) == 2
    assert [v.title for v in second.versions] == ["First", "Second"]
    assert second.latest is not None
    assert second.latest.title == "Second"

    reread = store.read(tmp_path)
    assert reread is not None
    assert [v.title for v in reread.versions] == ["First", "Second"]


def test_write_markdown_persists_the_file(tmp_path: Path) -> None:
    path = SummaryStore().write_markdown(tmp_path, "# Hello\n\nSome content.\n")
    assert path == tmp_path / "summary.md"
    assert path.read_text(encoding="utf-8") == "# Hello\n\nSome content.\n"


def test_write_markdown_overwrites_with_the_latest_rendering(tmp_path: Path) -> None:
    store = SummaryStore()
    store.write_markdown(tmp_path, "# First\n")
    store.write_markdown(tmp_path, "# Second\n")
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == "# Second\n"


def test_a_summary_json_written_before_segment_refs_existed_still_loads(tmp_path: Path) -> None:
    """Backward compatibility: Phase 5F added ``Decision.segment_refs`` /
    ``ActionItem.segment_refs`` additively — an older ``summary.json`` with neither key
    present must still load, with both defaulting to an empty list."""
    legacy_payload = {
        "session_id": "s1",
        "versions": [
            {
                "schema_version": 1,
                "title": "Legacy sync",
                "executive_summary": "Old format, no segment_refs at all.",
                "topics": [],
                "decisions": [{"description": "An old decision"}],
                "action_items": [
                    {"description": "An old action item", "owner": None, "deadline": None}
                ],
                "risks": [],
                "follow_ups": [],
                "provider": "ollama",
                "model_name": "llama3.1",
                "generated_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    (tmp_path / "summary.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = SummaryStore().read(tmp_path)
    assert loaded is not None
    assert isinstance(loaded, SummarySet)
    assert loaded.versions[0].decisions[0].segment_refs == []
    assert loaded.versions[0].action_items[0].segment_refs == []
