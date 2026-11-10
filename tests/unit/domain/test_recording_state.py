from __future__ import annotations

import itertools

import pytest

from local_meeting_recorder.domain import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    RecordingState,
    assert_transition,
    can_transition,
)

S = RecordingState

EXPECTED = {
    (S.IDLE, S.PREPARING),
    (S.PREPARING, S.RECORDING),
    (S.PREPARING, S.FINALIZING),
    (S.PREPARING, S.IDLE),
    (S.PREPARING, S.ERROR),
    (S.RECORDING, S.FINALIZING),
    (S.RECORDING, S.ERROR),
    (S.FINALIZING, S.SAVED),
    (S.FINALIZING, S.ERROR),
    (S.SAVED, S.IDLE),
    (S.ERROR, S.IDLE),
}


@pytest.mark.parametrize(("current", "new"), list(itertools.product(S, S)))
def test_every_pair_matches_the_table(current: S, new: S) -> None:
    allowed = (current, new) in EXPECTED
    assert can_transition(current, new) is allowed
    if allowed:
        assert_transition(current, new)
    else:
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(current, new)
        assert current.value in str(exc_info.value)
        assert new.value in str(exc_info.value)


def test_every_state_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_no_self_transitions() -> None:
    for state in S:
        assert state not in ALLOWED_TRANSITIONS[state]


def test_duplicate_start_and_stop_are_illegal() -> None:
    assert not can_transition(S.PREPARING, S.PREPARING)
    assert not can_transition(S.RECORDING, S.PREPARING)
    assert not can_transition(S.FINALIZING, S.FINALIZING)
    assert not can_transition(S.IDLE, S.FINALIZING)


def test_active_flag_and_labels() -> None:
    assert {s for s in S if s.is_active} == {S.PREPARING, S.RECORDING, S.FINALIZING}
    assert S.RECORDING.label == "Recording"
    assert all(s.label for s in S)
