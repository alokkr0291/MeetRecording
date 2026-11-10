"""Hardware acceptance test: real screen + system-audio + microphone capture on this Mac.

Gated by ``LMR_HW_TESTS=1``. This is the only test in the suite that proves capture
actually works; every other capture test uses a fake helper. A skip here is not a pass —
read the printed reason. See TESTING.md for the full procedure and interpretation guide.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from local_meeting_recorder.capture.macos.backend import MacOSCaptureBackend
from local_meeting_recorder.capture.protocols import StartOptions
from local_meeting_recorder.media.inspector import MediaInspector, is_audible
from local_meeting_recorder.storage.paths import resolve_helper_binary

HW_TESTS_ENV = "LMR_HW_TESTS"

pytestmark = pytest.mark.skipif(
    os.environ.get(HW_TESTS_ENV) != "1",
    reason=f"set {HW_TESTS_ENV}=1 to run real capture against this Mac's hardware",
)


def _generate_tone_wav(
    path: Path, *, freq_hz: float = 440.0, seconds: float = 6.0, sample_rate: int = 44_100
) -> None:
    """A mono sine WAV via the standard library only — no numpy, no ffmpeg."""
    amplitude = 0.6
    frame_count = int(seconds * sample_rate)
    frames = bytearray()
    for i in range(frame_count):
        sample = amplitude * math.sin(2 * math.pi * freq_hz * (i / sample_rate))
        frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def test_real_capture_produces_valid_media_with_audible_tracks(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("hardware capture test only runs on macOS")

    helper_path = resolve_helper_binary()
    if helper_path is None:
        pytest.skip("lmr-capture-helper is not built; run scripts/build_helper.sh")

    backend = MacOSCaptureBackend(helper_path=helper_path)
    permissions = backend.check_permissions(timeout=10)
    if not permissions.screen.is_usable:
        pytest.skip(
            f"Screen Recording permission is {permissions.screen.value}; "
            "grant it in System Settings and retry"
        )
    if not permissions.microphone.is_usable:
        pytest.skip(
            f"Microphone permission is {permissions.microphone.value}; "
            "grant it in System Settings and retry"
        )

    output = tmp_path / "recording.mov"
    tone_path = tmp_path / "tone.wav"
    _generate_tone_wav(tone_path)

    options = StartOptions(output_path=output, capture_microphone=True)
    started = backend.start(options, timeout=20)
    assert started.capture_microphone

    try:
        time.sleep(2.0)  # let the stream settle before the tone starts
        player = subprocess.Popen(["afplay", str(tone_path)])
        time.sleep(6.5)
        player.wait(timeout=5)
    finally:
        stopped = backend.stop(timeout=30)

    assert stopped.finalized, f"recording did not finalize: {stopped.error}"

    pgrep = subprocess.run(
        ["pgrep", "-f", "lmr-capture-helper"], check=False, capture_output=True, text=True
    )
    assert pgrep.returncode != 0, f"helper process still running after stop: {pgrep.stdout}"

    inspector = MediaInspector()
    summary = inspector.probe(output)
    assert len(summary.video_streams) == 1, "expected exactly one video stream"
    assert len(summary.audio_streams) == 2, "expected system-audio + microphone audio streams"
    video = summary.video_streams[0]
    assert (video.nb_frames or 0) > 0, "video stream has zero frames"

    system_track, mic_track = summary.audio_streams
    system_rms = inspector.measure_rms_db(output, system_track.index)
    mic_rms = inspector.measure_rms_db(output, mic_track.index)
    report = (
        f"system_rms_db={system_rms} mic_rms_db={mic_rms} "
        f"video_offset_s={stopped.first_video_offset_s} "
        f"system_offset_s={stopped.first_system_audio_offset_s} "
        f"mic_offset_s={stopped.first_mic_audio_offset_s}"
    )
    print(report)

    if stopped.first_video_offset_s is not None and stopped.first_system_audio_offset_s is not None:
        sync_error_ms = (
            abs(stopped.first_video_offset_s - stopped.first_system_audio_offset_s) * 1000
        )
        assert sync_error_ms < 250, f"A/V sync offset too large: {sync_error_ms:.1f} ms ({report})"

    if not is_audible(system_rms):
        pytest.fail(
            "System audio track is silent: ScreenCaptureKit audio was not delivered. "
            f"Check the Screen Recording grant and that system output is not muted. {report}"
        )
    if not is_audible(mic_rms):
        pytest.fail(
            "Microphone track is silent: check the Microphone grant, the default input "
            f"device, and that the speaker and microphone are acoustically coupled "
            f"(not headphones). {report}"
        )
