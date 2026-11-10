from __future__ import annotations

from local_meeting_recorder.capture.protocols import PermissionState, PermissionStatus
from local_meeting_recorder.ui.permission_guide import (
    MICROPHONE_SETTINGS_URL,
    SCREEN_RECORDING_SETTINGS_URL,
    describe_missing_permissions,
    open_microphone_settings,
    open_screen_recording_settings,
)


def test_describe_missing_permissions_lists_both_when_both_missing() -> None:
    status = PermissionStatus(
        screen=PermissionState.DENIED, microphone=PermissionState.NOT_DETERMINED
    )
    text = describe_missing_permissions(status)
    assert "Screen Recording" in text
    assert "Microphone" in text


def test_describe_missing_permissions_when_all_granted() -> None:
    status = PermissionStatus(screen=PermissionState.GRANTED, microphone=PermissionState.GRANTED)
    assert describe_missing_permissions(status) == "All required permissions are granted."


def test_describe_missing_permissions_lists_only_the_denied_one() -> None:
    status = PermissionStatus(screen=PermissionState.GRANTED, microphone=PermissionState.DENIED)
    text = describe_missing_permissions(status)
    assert "Microphone" in text
    assert "Screen Recording" not in text


def test_open_screen_recording_settings_calls_runner_with_the_right_url() -> None:
    calls: list[list[str]] = []
    open_screen_recording_settings(runner=calls.append)
    assert calls == [["open", SCREEN_RECORDING_SETTINGS_URL]]


def test_open_microphone_settings_calls_runner_with_the_right_url() -> None:
    calls: list[list[str]] = []
    open_microphone_settings(runner=calls.append)
    assert calls == [["open", MICROPHONE_SETTINGS_URL]]
