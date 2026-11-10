from __future__ import annotations

from datetime import datetime

from local_meeting_recorder.domain import is_valid_session_id, new_session_id


def test_format_and_validation() -> None:
    sid = new_session_id(datetime(2026, 10, 15, 14, 30, 22))
    assert sid.startswith("lmr_20261015T143022_")
    assert is_valid_session_id(sid)
    assert len(sid) == len("lmr_20261015T143022_abcd")


def test_uniqueness_in_same_second() -> None:
    now = datetime(2026, 1, 1, 0, 0, 0)
    ids = {new_session_id(now) for _ in range(200)}
    assert len(ids) > 190  # 16 bits of randomness; collisions are statistically negligible


def test_rejects_garbage() -> None:
    for bad in ("", "lmr_", "lmr_20261015T143022", "../etc", "lmr_20261015T143022_zzzz "):
        assert not is_valid_session_id(bad)


def test_sortable_by_time() -> None:
    a = new_session_id(datetime(2026, 1, 1, 0, 0, 0))
    b = new_session_id(datetime(2026, 1, 1, 0, 0, 1))
    assert a < b
