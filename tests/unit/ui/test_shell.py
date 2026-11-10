from __future__ import annotations

from pathlib import Path

from local_meeting_recorder.ui.shell import open_path, open_url


def test_open_path_creates_missing_directory_then_calls_runner(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist"
    calls: list[list[str]] = []
    open_path(target, runner=calls.append)
    assert target.is_dir()
    assert calls == [["open", str(target)]]


def test_open_path_does_not_recreate_an_existing_directory(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    open_path(tmp_path, runner=calls.append)
    assert calls == [["open", str(tmp_path)]]


def test_open_url_calls_runner_with_open_and_the_url() -> None:
    calls: list[list[str]] = []
    open_url("x-apple.systempreferences:com.apple.preference.security", runner=calls.append)
    assert calls == [["open", "x-apple.systempreferences:com.apple.preference.security"]]


def test_open_path_swallows_runner_os_error(tmp_path: Path) -> None:
    def failing_runner(_argv: list[str]) -> None:
        raise OSError("no such application")

    open_path(tmp_path, runner=failing_runner)  # must not raise
