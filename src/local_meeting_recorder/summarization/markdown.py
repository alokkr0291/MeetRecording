"""Renders a ``MeetingSummary`` (plus the session and transcript it came from) as a
human-readable ``summary.md`` — Priority 3 of the Phase 5 stabilization pass.

Pure rendering, no I/O: ``SummaryStore.write_markdown`` owns persistence. Never invents
content — every section only restates data already present on ``summary``, ``metadata``,
or ``transcript``; a timestamp reference is included only when the cited segment index
actually resolves against the transcript this summary was generated from.
"""

from __future__ import annotations

from local_meeting_recorder.domain.session_metadata import SessionMetadata
from local_meeting_recorder.summarization.models import MeetingSummary
from local_meeting_recorder.transcription.models import Transcript


def render_summary_markdown(
    summary: MeetingSummary, *, metadata: SessionMetadata, transcript: Transcript | None
) -> str:
    title = summary.title.strip() or metadata.title or metadata.session_id
    meeting_date = metadata.started_at or metadata.created_at

    lines: list[str] = [f"# {title}", ""]
    lines.append(f"**Date:** {meeting_date.isoformat()}")
    lines.append(f"**Provider:** {summary.provider} ({summary.model_name})")
    lines.append(f"**Generated:** {summary.generated_at.isoformat()}")
    lines.append("")

    lines.append("## Executive Summary")
    lines.append("")
    lines.append(summary.executive_summary)
    lines.append("")

    if summary.topics:
        lines.append("## Key Topics")
        lines.append("")
        lines.extend(f"- {topic}" for topic in summary.topics)
        lines.append("")

    if summary.decisions:
        lines.append("## Decisions")
        lines.append("")
        for decision in summary.decisions:
            evidence = _evidence_suffix(decision.segment_refs, transcript)
            lines.append(f"- {decision.description}{evidence}")
        lines.append("")

    if summary.action_items:
        lines.append("## Action Items")
        lines.append("")
        for item in summary.action_items:
            owner = f" — **{item.owner}**" if item.owner else ""
            deadline = f" (due {item.deadline})" if item.deadline else ""
            evidence = _evidence_suffix(item.segment_refs, transcript)
            lines.append(f"- {item.description}{owner}{deadline}{evidence}")
        lines.append("")

    if summary.risks:
        lines.append("## Risks")
        lines.append("")
        lines.extend(f"- {risk}" for risk in summary.risks)
        lines.append("")

    if summary.follow_ups:
        lines.append("## Follow-ups")
        lines.append("")
        lines.extend(f"- {follow_up}" for follow_up in summary.follow_ups)
        lines.append("")

    speakers = transcript.speakers if transcript is not None else []
    if speakers:
        lines.append("## Speakers")
        lines.append("")
        lines.extend(f"- {speaker.display_name}" for speaker in speakers)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _evidence_suffix(segment_refs: list[int], transcript: Transcript | None) -> str:
    """A ``" _(see 00:42, 01:10)_"``-style suffix for whichever cited segment indices
    actually resolve against ``transcript`` — silently drops (never fabricates a
    timestamp for) a reference that is out of range or that there's no transcript to
    resolve against at all."""
    if not segment_refs or transcript is None:
        return ""
    timestamps = [
        _format_timestamp(transcript.segments[ref].start_s)
        for ref in segment_refs
        if 0 <= ref < len(transcript.segments)
    ]
    if not timestamps:
        return ""
    return f" _(see {', '.join(timestamps)})_"


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"
