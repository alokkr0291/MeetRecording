"""Structured meeting summary schema.

Separate from ``transcription.models`` on purpose: a summary is model output distilled
from a transcript, not a recording artifact. ``owner``/``deadline`` are optional because
the generation prompt (see ``schema.py``) instructs the model to leave them ``null``
rather than invent one — enforced by instruction, not something this schema can verify on
its own, so never treat a filled-in owner/deadline as ground truth beyond "the model
believed the transcript said this."
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from local_meeting_recorder.domain.session_metadata import utc_now

SUMMARY_SCHEMA_VERSION = 1


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    segment_refs: list[int] = Field(default_factory=list)
    """Positions into the transcript this summary was generated from
    (``Transcript.segments[i]``) that support this decision, when the model could
    identify them — empty when it could not. Additive field: a ``summary.json`` written
    before Phase 5F has no ``segment_refs`` key and loads with this default."""


class ActionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    owner: str | None = None
    deadline: str | None = None
    segment_refs: list[int] = Field(default_factory=list)
    """Same meaning as ``Decision.segment_refs`` — see that field's docstring."""


class MeetingSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = SUMMARY_SCHEMA_VERSION
    title: str
    executive_summary: str
    topics: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    provider: str
    model_name: str
    generated_at: datetime = Field(default_factory=utc_now)


class SummarySet(BaseModel):
    """Every summary ever generated for one session, oldest first — "preserve previous
    summaries" when a user regenerates with a different provider/model rather than
    silently discarding the last one."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    versions: list[MeetingSummary] = Field(default_factory=list)

    @property
    def latest(self) -> MeetingSummary | None:
        return self.versions[-1] if self.versions else None
