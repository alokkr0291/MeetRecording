"""Real-model acceptance test: an actual ``faster-whisper`` model loaded and run against
real synthesized speech (macOS ``say``, converted to 16 kHz mono WAV via ffmpeg).

Gated by ``LMR_WHISPER_TESTS=1`` — this downloads a model on first run (the "tiny" model,
~75 MB) and does real CPU inference, so it is not part of the default unit/integration
suite. A skip here is not a pass; read the printed reason. See TESTING.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from local_meeting_recorder.transcription.engine import TranscriptionEngine

WHISPER_TESTS_ENV = "LMR_WHISPER_TESTS"

pytestmark = pytest.mark.skipif(
    os.environ.get(WHISPER_TESTS_ENV) != "1",
    reason=f"set {WHISPER_TESTS_ENV}=1 to run a real faster-whisper model (downloads on first run)",
)

SPOKEN_TEXT = "The quick brown fox jumps over the lazy dog near the river bank."


def _synthesize_speech_wav(path: Path, *, text: str = SPOKEN_TEXT) -> None:
    """Real spoken audio via macOS's built-in ``say``, converted to 16 kHz mono WAV."""
    aiff_path = path.with_suffix(".aiff")
    subprocess.run(["say", "-o", str(aiff_path), text], check=True, timeout=30)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(aiff_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(path),
        ],
        check=True,
        timeout=30,
    )


def test_real_model_transcribes_real_speech(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("uses macOS's `say` to synthesize test speech")
    if shutil.which("say") is None:
        pytest.skip("`say` not found on PATH")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not found on PATH")

    wav_path = tmp_path / "speech.wav"
    _synthesize_speech_wav(wav_path)

    engine = TranscriptionEngine(model_size="tiny", device="cpu", compute_type="int8")
    result = engine.transcribe(wav_path, language="en")

    assert result.language == "en"
    assert result.segments, "expected at least one transcribed segment"
    full_text = " ".join(s.text for s in result.segments).lower()
    # Real ASR output rarely matches verbatim; check for recognizable fragments instead
    # of an exact match.
    assert any(word in full_text for word in ("fox", "dog", "quick", "brown", "river"))
