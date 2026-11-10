from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_meeting_recorder.infrastructure.single_instance import (
    AlreadyRunningError,
    SingleInstanceGuard,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="SingleInstanceGuard is a no-op on Windows until Phase 7"
)


def test_first_instance_acquires_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "app.lock"
    with SingleInstanceGuard(lock_path):
        assert lock_path.is_file()


def test_second_instance_raises_while_first_holds_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "app.lock"
    with (
        SingleInstanceGuard(lock_path),
        pytest.raises(AlreadyRunningError),
        SingleInstanceGuard(lock_path),
    ):
        pass


def test_lock_is_released_on_exit_so_a_later_instance_can_acquire_it(tmp_path: Path) -> None:
    lock_path = tmp_path / "app.lock"
    with SingleInstanceGuard(lock_path):
        pass
    with SingleInstanceGuard(lock_path):
        pass  # must not raise: the first guard released the lock on __exit__
