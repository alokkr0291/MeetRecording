"""AudioExtractor against real ffmpeg-synthesized files.

Needs ffmpeg/ffprobe on PATH (see TESTING.md); the whole module skips with the exact
reason when they are missing, same pattern as ``test_inspector.py``.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from local_meeting_recorder.domain.errors import MediaError, NoAudioStreamError
from local_meeting_recorder.media.audio_extractor import AudioExtractor

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH",
)


def _synthesize_with_audio(path: Path, *, audio_streams: int = 2) -> None:
    """One video stream + ``audio_streams`` audio streams, like the helper's output."""
    argv = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=10:duration=1",
    ]
    for hz in (440, 220)[:audio_streams]:
        argv += ["-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration=1"]
    argv += ["-map", "0:v"]
    for i in range(audio_streams):
        argv += ["-map", f"{i + 1}:a"]
    argv += ["-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)


def _synthesize_video_only(path: Path) -> None:
    argv = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=10:duration=1",
        "-c:v",
        "libx264",
        str(path),
    ]
    subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)


def test_extracts_mono_16k_wav_from_multi_track_recording(tmp_path: Path) -> None:
    source = tmp_path / "recording.mov"
    _synthesize_with_audio(source, audio_streams=2)
    dest = tmp_path / "audio_16k_mono.wav"

    result = AudioExtractor().extract_wav16k_mono(source, dest)

    assert result == dest
    assert dest.is_file()
    with wave.open(str(dest), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnframes() > 0


def test_extracts_from_single_audio_track_recording(tmp_path: Path) -> None:
    source = tmp_path / "recording.mov"
    _synthesize_with_audio(source, audio_streams=1)
    dest = tmp_path / "audio_16k_mono.wav"

    AudioExtractor().extract_wav16k_mono(source, dest)
    with wave.open(str(dest), "rb") as wav_file:
        assert wav_file.getnframes() > 0


def test_missing_audio_stream_raises(tmp_path: Path) -> None:
    source = tmp_path / "video_only.mov"
    _synthesize_video_only(source)
    dest = tmp_path / "audio_16k_mono.wav"

    with pytest.raises(NoAudioStreamError):
        AudioExtractor().extract_wav16k_mono(source, dest)
    assert not dest.exists()


def test_invalid_media_raises_media_error(tmp_path: Path) -> None:
    source = tmp_path / "garbage.mov"
    source.write_bytes(b"this is not a real media file" * 100)
    dest = tmp_path / "audio_16k_mono.wav"

    with pytest.raises(MediaError):
        AudioExtractor().extract_wav16k_mono(source, dest)


def test_missing_source_file_raises_media_error(tmp_path: Path) -> None:
    with pytest.raises(MediaError):
        AudioExtractor().extract_wav16k_mono(tmp_path / "nonexistent.mov", tmp_path / "out.wav")


# -- extract_track_wav16k_mono (Phase 5: source-separated extraction, no mixing) ----------


def test_extract_track_isolates_one_stream_without_mixing(tmp_path: Path) -> None:
    """Matches the real helper's layout: video=0, system audio=1, mic audio=2 — extracting
    each independently must produce genuinely different content, not a coincidental mix."""
    source = tmp_path / "recording.mov"
    _synthesize_with_audio(source, audio_streams=2)
    system_wav = tmp_path / "system.wav"
    mic_wav = tmp_path / "mic.wav"

    AudioExtractor().extract_track_wav16k_mono(source, system_wav, stream_index=1)
    AudioExtractor().extract_track_wav16k_mono(source, mic_wav, stream_index=2)

    assert system_wav.read_bytes() != mic_wav.read_bytes()
    for wav_path in (system_wav, mic_wav):
        with wave.open(str(wav_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getframerate() == 16_000
            assert wav_file.getnframes() > 0


def test_extract_track_with_unknown_index_raises(tmp_path: Path) -> None:
    source = tmp_path / "recording.mov"
    _synthesize_with_audio(source, audio_streams=2)
    dest = tmp_path / "out.wav"

    with pytest.raises(NoAudioStreamError):
        AudioExtractor().extract_track_wav16k_mono(source, dest, stream_index=99)
    assert not dest.exists()


def test_extract_track_from_video_only_recording_raises(tmp_path: Path) -> None:
    source = tmp_path / "video_only.mov"
    _synthesize_video_only(source)
    dest = tmp_path / "out.wav"

    with pytest.raises(NoAudioStreamError):
        AudioExtractor().extract_track_wav16k_mono(source, dest, stream_index=1)
