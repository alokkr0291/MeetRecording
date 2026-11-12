"""``render_summary_markdown``: pure rendering, no I/O — never invents content beyond
what the summary/metadata/transcript already contain."""

from __future__ import annotations

from datetime import datetime

from local_meeting_recorder.domain.session_metadata import SessionMetadata
from local_meeting_recorder.summarization.markdown import render_summary_markdown
from local_meeting_recorder.summarization.models import ActionItem, Decision, MeetingSummary
from local_meeting_recorder.transcription.models import (
    AudioSource,
    Speaker,
    Transcript,
    TranscriptSegment,
)


def _summary(**overrides: object) -> MeetingSummary:
    defaults: dict[str, object] = dict(
        title="Weekly Sync",
        executive_summary="The team discussed the roadmap.",
        topics=["roadmap", "hiring"],
        decisions=[Decision(description="Ship the beta Friday", segment_refs=[0])],
        action_items=[
            ActionItem(
                description="Write the launch doc",
                owner="Alex",
                deadline="Friday",
                segment_refs=[1],
            )
        ],
        risks=["launch date is tight"],
        follow_ups=["revisit next sync"],
        provider="ollama",
        model_name="llama3.1",
    )
    defaults.update(overrides)
    return MeetingSummary(**defaults)  # type: ignore[arg-type]


def _transcript() -> Transcript:
    return Transcript(
        session_id="lmr_20261110T000000_aaaa",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="let's ship by Friday",
                source=AudioSource.SYSTEM,
                speaker_id="speaker_1",
            ),
            TranscriptSegment(
                start_s=70.0,
                end_s=72.0,
                text="I'll write the doc",
                source=AudioSource.MICROPHONE,
                speaker_id="user",
            ),
        ],
        speakers=[
            Speaker(
                speaker_id="speaker_1", display_name="Speaker 1", source_type=AudioSource.SYSTEM
            ),
            Speaker(speaker_id="user", display_name="You", source_type=AudioSource.MICROPHONE),
        ],
    )


def test_includes_title_date_provider_and_model() -> None:
    metadata = SessionMetadata(
        session_id="lmr_20261110T000000_aaaa",
        started_at=datetime(2026, 11, 10, 22, 0, 0),
    )
    markdown = render_summary_markdown(_summary(), metadata=metadata, transcript=None)
    assert markdown.startswith("# Weekly Sync")
    assert "2026-11-10" in markdown
    assert "ollama" in markdown
    assert "llama3.1" in markdown


def test_falls_back_to_session_id_when_summary_has_no_title() -> None:
    metadata = SessionMetadata(session_id="lmr_20261110T000000_aaaa")
    markdown = render_summary_markdown(_summary(title=""), metadata=metadata, transcript=None)
    assert "lmr_20261110T000000_aaaa" in markdown.splitlines()[0]


def test_includes_executive_summary_topics_decisions_and_action_items() -> None:
    metadata = SessionMetadata(session_id="s1")
    markdown = render_summary_markdown(_summary(), metadata=metadata, transcript=None)
    assert "The team discussed the roadmap." in markdown
    assert "- roadmap" in markdown
    assert "Ship the beta Friday" in markdown
    assert "Write the launch doc" in markdown
    assert "Alex" in markdown
    assert "Friday" in markdown


def test_includes_speaker_attribution_when_a_transcript_is_available() -> None:
    metadata = SessionMetadata(session_id="s1")
    markdown = render_summary_markdown(_summary(), metadata=metadata, transcript=_transcript())
    assert "## Speakers" in markdown
    assert "Speaker 1" in markdown
    assert "You" in markdown


def test_omits_speakers_section_without_a_transcript() -> None:
    metadata = SessionMetadata(session_id="s1")
    markdown = render_summary_markdown(_summary(), metadata=metadata, transcript=None)
    assert "## Speakers" not in markdown


def test_resolves_segment_refs_to_timestamps() -> None:
    metadata = SessionMetadata(session_id="s1")
    markdown = render_summary_markdown(_summary(), metadata=metadata, transcript=_transcript())
    assert "00:00" in markdown  # decision's segment_refs=[0] -> start_s=0.0
    assert "01:10" in markdown  # action item's segment_refs=[1] -> start_s=70.0


def test_out_of_range_segment_ref_is_silently_dropped_not_fabricated() -> None:
    metadata = SessionMetadata(session_id="s1")
    summary = _summary(decisions=[Decision(description="Something", segment_refs=[99])])
    markdown = render_summary_markdown(summary, metadata=metadata, transcript=_transcript())
    assert "Something" in markdown
    assert "_(see" not in markdown.split("Something")[1].split("\n")[0]


def test_empty_lists_omit_their_sections() -> None:
    metadata = SessionMetadata(session_id="s1")
    summary = _summary(topics=[], decisions=[], action_items=[], risks=[], follow_ups=[])
    markdown = render_summary_markdown(summary, metadata=metadata, transcript=None)
    headings = ("## Key Topics", "## Decisions", "## Action Items", "## Risks", "## Follow-ups")
    for heading in headings:
        assert heading not in markdown
