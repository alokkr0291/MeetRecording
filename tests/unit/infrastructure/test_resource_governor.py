"""``ResourceGovernor``: app-wide (not per-session) caps on concurrent heavy AI jobs —
the "Low Resource" processing profile's serialization guarantee (Phase 6)."""

from __future__ import annotations

import threading
import time

from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor


def _wait_until(predicate: object, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.005)
    return predicate()  # type: ignore[operator]


def test_unlimited_by_default_never_blocks() -> None:
    governor = ResourceGovernor()
    entered = []
    with governor.heavy_job():
        entered.append("a")
        with governor.heavy_job():  # nested, same thread — must not deadlock itself
            entered.append("b")
    assert entered == ["a", "b"]


def test_limit_of_one_serializes_two_concurrent_jobs() -> None:
    governor = ResourceGovernor(max_concurrent_ai_jobs=1)
    order: list[str] = []
    second_started = threading.Event()

    def first() -> None:
        with governor.heavy_job():
            order.append("first-start")
            time.sleep(0.05)
            order.append("first-end")

    def second() -> None:
        with governor.heavy_job():
            second_started.set()
            order.append("second-start")
            order.append("second-end")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    time.sleep(0.01)  # let `first` actually acquire before starting `second`
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert order == ["first-start", "first-end", "second-start", "second-end"]


def test_a_crashed_job_releases_its_slot() -> None:
    governor = ResourceGovernor(max_concurrent_ai_jobs=1)

    try:
        with governor.heavy_job():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    acquired = []
    with governor.heavy_job():
        acquired.append(True)
    assert acquired == [True]


def test_set_limit_takes_effect_for_subsequent_jobs() -> None:
    governor = ResourceGovernor(max_concurrent_ai_jobs=1)
    assert governor.limit == 1
    governor.set_limit(0)
    assert governor.limit == 0

    # Unlimited now: two jobs can be "in flight" at once without either blocking.
    first_in = threading.Event()
    release_first = threading.Event()

    def holder() -> None:
        with governor.heavy_job():
            first_in.set()
            release_first.wait(timeout=2.0)

    t = threading.Thread(target=holder)
    t.start()
    assert _wait_until(first_in.is_set)

    second_ran = []
    with governor.heavy_job():  # must not block even though `holder` is still inside
        second_ran.append(True)
    assert second_ran == [True]

    release_first.set()
    t.join(timeout=2.0)


def test_raising_the_limit_wakes_a_waiting_job() -> None:
    governor = ResourceGovernor(max_concurrent_ai_jobs=1)
    first_in = threading.Event()
    release_first = threading.Event()
    second_in = threading.Event()

    def first() -> None:
        with governor.heavy_job():
            first_in.set()
            release_first.wait(timeout=2.0)

    def second() -> None:
        with governor.heavy_job():
            second_in.set()

    t1 = threading.Thread(target=first)
    t1.start()
    assert _wait_until(first_in.is_set)

    t2 = threading.Thread(target=second)
    t2.start()
    time.sleep(0.05)
    assert not second_in.is_set()  # still waiting — limit is 1 and `first` holds it

    governor.set_limit(2)  # raising the limit must wake the waiter without releasing anything
    assert _wait_until(second_in.is_set)

    release_first.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
