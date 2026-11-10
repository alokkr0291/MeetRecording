"""Parsing of real helper event shapes, and the start-command field names.

The ``capture_microphone`` (not ``capture_mic``) assertion pins down a mismatch found
between the original ARCHITECTURE.md sketch and native/macos/Sources/main.swift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_recorder.capture.protocols import (
    DisplaysInfo,
    ErrorInfo,
    PermissionState,
    ProbeInfo,
    ReadyInfo,
    StartedInfo,
    StartOptions,
    StatsInfo,
    StoppedInfo,
    parse_event,
)
from local_meeting_recorder.domain.errors import (
    CaptureError,
    HelperProtocolError,
    PermissionDeniedError,
)


def test_start_options_uses_real_field_name_capture_microphone() -> None:
    options = StartOptions(output_path=Path("/tmp/recording.mov"), capture_microphone=False)
    command = options.to_command()
    assert command["capture_microphone"] is False
    assert "capture_mic" not in command


def test_parse_ready_event() -> None:
    name, model = parse_event(
        {"event": "ready", "version": "0.1.0", "protocol": 1, "pid": 123, "macos": "26.2"}
    )
    assert name == "ready"
    assert isinstance(model, ReadyInfo)
    assert model.pid == 123


def test_parse_probe_event_with_nested_permissions() -> None:
    name, model = parse_event(
        {
            "event": "probe",
            "version": "0.1.0",
            "protocol": 1,
            "macos": "26.2",
            "permissions": {"screen": "granted", "screen_preflight": True, "microphone": "denied"},
        }
    )
    assert name == "probe"
    assert isinstance(model, ProbeInfo)
    assert model.permissions.screen is PermissionState.GRANTED
    assert model.permissions.microphone is PermissionState.DENIED
    assert not model.permissions.all_granted


def test_parse_displays_event_with_real_fields() -> None:
    name, model = parse_event(
        {
            "event": "displays",
            "displays": [
                {
                    "id": 1,
                    "name": "Built-in",
                    "width": 1920,
                    "height": 1080,
                    "pixel_width": 3840,
                    "pixel_height": 2160,
                    "is_main": True,
                },
            ],
        }
    )
    assert name == "displays"
    assert isinstance(model, DisplaysInfo)
    assert model.displays[0].pixel_width == 3840


def test_parse_started_event_has_no_session_id_field() -> None:
    _name, model = parse_event(
        {
            "event": "started",
            "output_path": "/tmp/r.mov",
            "display_id": 1,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "capture_microphone": True,
            "tracks": [{"index": 0, "kind": "video", "codec": "h264"}],
        }
    )
    assert isinstance(model, StartedInfo)
    assert not hasattr(model, "session_id")


def test_parse_rich_stats_event() -> None:
    _name, model = parse_event(
        {
            "event": "stats",
            "elapsed_s": 5.0,
            "bytes": 44100,
            "free_mb": 12000.0,
            "session_started": True,
            "video_frames": 150,
            "system_audio_buffers": 10,
            "mic_audio_buffers": 10,
            "media_duration_s": 5.1,
        }
    )
    assert isinstance(model, StatsInfo)
    assert model.video_frames == 150
    assert model.media_duration_s == 5.1


def test_parse_stopped_event() -> None:
    _name, model = parse_event(
        {
            "event": "stopped",
            "reason": "requested",
            "finalized": True,
            "output_path": "/tmp/r.mov",
            "bytes": 44100,
            "duration_s": 5.1,
            "elapsed_s": 5.2,
        }
    )
    assert isinstance(model, StoppedInfo)
    assert model.finalized


def test_error_event_permission_denied_maps_to_domain_exception() -> None:
    _name, model = parse_event(
        {
            "event": "error",
            "code": "permission_denied",
            "message": "no screen access",
            "fatal": True,
            "permission": "screen",
        }
    )
    assert isinstance(model, ErrorInfo)
    exc = model.to_exception()
    assert isinstance(exc, PermissionDeniedError)
    assert exc.permission == "screen"


def test_error_event_other_code_maps_to_capture_error() -> None:
    _name, model = parse_event(
        {
            "event": "error",
            "code": "disk_full",
            "message": "no space left",
            "fatal": True,
        }
    )
    assert isinstance(model, ErrorInfo)
    exc = model.to_exception()
    assert isinstance(exc, CaptureError)
    assert exc.code == "disk_full"


def test_missing_event_field_raises_protocol_error() -> None:
    with pytest.raises(HelperProtocolError):
        parse_event({"version": "0.1.0"})


def test_unknown_event_name_raises_protocol_error() -> None:
    with pytest.raises(HelperProtocolError):
        parse_event({"event": "something_new_the_helper_added"})


def test_malformed_event_payload_raises_protocol_error() -> None:
    with pytest.raises(HelperProtocolError):
        parse_event({"event": "ready", "version": "0.1.0"})  # missing protocol/pid/macos
