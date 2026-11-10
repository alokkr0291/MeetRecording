"""Recording lifecycle states and the legal transitions between them.

The coordinator is the only component allowed to change state, and it must call
``assert_transition`` before doing so. Keeping the table here (pure data) lets the
unit tests enumerate every pair of states.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from local_meeting_recorder.domain.errors import InvalidTransitionError


class RecordingState(StrEnum):
    """Externally visible lifecycle states."""

    IDLE = "idle"
    PREPARING = "preparing"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    SAVED = "saved"
    ERROR = "error"

    @property
    def is_active(self) -> bool:
        """True while a session owns capture resources."""
        return self in {
            RecordingState.PREPARING,
            RecordingState.RECORDING,
            RecordingState.FINALIZING,
        }

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS: Mapping[RecordingState, str] = MappingProxyType(
    {
        RecordingState.IDLE: "Idle",
        RecordingState.PREPARING: "Preparing…",
        RecordingState.RECORDING: "Recording",
        RecordingState.FINALIZING: "Finalizing…",
        RecordingState.SAVED: "Saved",
        RecordingState.ERROR: "Error",
    }
)

ALLOWED_TRANSITIONS: Mapping[RecordingState, frozenset[RecordingState]] = MappingProxyType(
    {
        RecordingState.IDLE: frozenset({RecordingState.PREPARING}),
        # Preparing -> Idle: consent declined or stop requested before the helper produced media.
        # Preparing -> Finalizing: stop requested after the helper was launched; finalize cleanly.
        RecordingState.PREPARING: frozenset(
            {
                RecordingState.RECORDING,
                RecordingState.FINALIZING,
                RecordingState.IDLE,
                RecordingState.ERROR,
            }
        ),
        RecordingState.RECORDING: frozenset({RecordingState.FINALIZING, RecordingState.ERROR}),
        RecordingState.FINALIZING: frozenset({RecordingState.SAVED, RecordingState.ERROR}),
        RecordingState.SAVED: frozenset({RecordingState.IDLE}),
        RecordingState.ERROR: frozenset({RecordingState.IDLE}),
    }
)


def can_transition(current: RecordingState, new: RecordingState) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def assert_transition(current: RecordingState, new: RecordingState) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(
            f"Cannot move from {current.value} to {new.value}; "
            f"allowed: {sorted(s.value for s in ALLOWED_TRANSITIONS[current])}"
        )
