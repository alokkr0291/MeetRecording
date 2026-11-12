"""Shared shape for anything that can turn a transcript into a ``MeetingSummary``.
``SummaryManager`` depends only on this protocol, never a concrete provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from local_meeting_recorder.summarization.models import MeetingSummary


class SummaryProviderClient(Protocol):
    model_name: str

    def generate_summary(self, transcript_text: str) -> MeetingSummary: ...


@dataclass(frozen=True)
class SummaryConnectionTestResult:
    ok: bool
    message: str
