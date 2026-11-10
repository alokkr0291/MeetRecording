"""MediaInspector against real ffmpeg-synthesized files.

Needs ffmpeg/ffprobe on PATH (see TESTING.md); the whole module skips with the exact
reason when they are missing, rather than silently passing nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from local_meeting_recorder.domain.errors import MediaError
from local_meeting_recorder.media.inspector import MediaInspector, is_audible

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH",
)


def _synthesize(path: Path, *, tone_hz: int = 440, silent: bool = False) -> None:
    """One video stream + two audio streams, matching the helper's track layout."""
    audio_source = (
        "anullsrc=sample_rate=48000:channel_layout=stereo:duration=1"
        if silent
        else f"sine=frequency={tone_hz}:sample_rate=48000:duration=1"
    )
    argv = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=10:duration=1",
        "-f",
        "lavfi",
        "-i",
        audio_source,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:sample_rate=48000:duration=1",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)


def test_probe_reports_expected_stream_shape(tmp_path: Path) -> None:
    target = tmp_path / "recording.mov"
    _synthesize(target)
    summary = MediaInspector().probe(target)
    assert len(summary.video_streams) == 1
    assert len(summary.audio_streams) == 2
    assert summary.duration_s > 0
    video = summary.video_streams[0]
    assert video.width == 320
    assert video.height == 240


def test_rms_detects_tone_as_audible(tmp_path: Path) -> None:
    target = tmp_path / "recording.mov"
    _synthesize(target)
    inspector = MediaInspector()
    summary = inspector.probe(target)
    system_track = summary.audio_streams[0]
    rms = inspector.measure_rms_db(target, system_track.index)
    assert is_audible(rms)


def test_rms_detects_silence_as_inaudible(tmp_path: Path) -> None:
    target = tmp_path / "recording.mov"
    _synthesize(target, silent=True)
    inspector = MediaInspector()
    summary = inspector.probe(target)
    system_track = summary.audio_streams[0]
    rms = inspector.measure_rms_db(target, system_track.index)
    assert not is_audible(rms)


def test_probe_missing_file_raises_media_error() -> None:
    with pytest.raises(MediaError):
        MediaInspector().probe(Path("/nonexistent/recording.mov"))


def test_probe_with_bad_ffprobe_path_raises_media_error(tmp_path: Path) -> None:
    target = tmp_path / "recording.mov"
    _synthesize(target)
    with pytest.raises(MediaError):
        MediaInspector(ffprobe_path="/nonexistent/ffprobe").probe(target)
