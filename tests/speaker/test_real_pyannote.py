"""Real-model acceptance test: the actual ``pyannote/speaker-diarization-3.1`` model,
downloaded and run for real (not the mocked ``Pipeline`` used by ``tests/unit/speaker/``).

Gated by ``LMR_DIARIZATION_TESTS=1`` *and* a Hugging Face access token in one of
``LMR_HF_TOKEN``/``HUGGINGFACE_TOKEN``/``HF_TOKEN`` — downloading this gated model
requires that token's account to have already accepted the model's terms of use on
huggingface.co. Not part of the default unit/integration suite (``scripts/test.sh`` only
runs ``tests/unit``/``tests/integration``). A skip here is not a pass; read the printed
reason.

Uses a single macOS ``say`` voice, like ``tests/transcription/test_real_whisper.py`` —
this only proves the real pipeline downloads and runs end to end on real (if synthetic)
speech. It is NOT evidence of real-world multi-speaker diarization accuracy; that needs
a genuine multi-participant recording, which this environment does not have. See the
engineering audit / IMPLEMENTATION_STATUS.md for that open gap.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from local_meeting_recorder.speaker.pyannote_engine import PyannoteSpeakerDiarizationEngine

DIARIZATION_TESTS_ENV = "LMR_DIARIZATION_TESTS"
HF_TOKEN_ENV_VARS = ("LMR_HF_TOKEN", "HUGGINGFACE_TOKEN", "HF_TOKEN")

pytestmark = pytest.mark.skipif(
    os.environ.get(DIARIZATION_TESTS_ENV) != "1",
    reason=(
        f"set {DIARIZATION_TESTS_ENV}=1 to run the real pyannote model (downloads on first run)"
    ),
)


def _hf_token() -> str | None:
    for name in HF_TOKEN_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _synthesize_speech_wav(path: Path) -> None:
    aiff_path = path.with_suffix(".aiff")
    subprocess.run(
        ["say", "-o", str(aiff_path), "Let's discuss the roadmap for next quarter."],
        check=True,
        timeout=30,
    )
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


def test_real_model_downloads_and_runs_end_to_end(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("uses macOS's `say` to synthesize test speech")
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("`say`/ffmpeg not found on PATH")
    if importlib.util.find_spec("pyannote.audio") is None:
        pytest.skip("pyannote.audio not installed — install the 'diarization' extra to run this")
    token = _hf_token()
    if token is None:
        pytest.skip(
            f"set one of {HF_TOKEN_ENV_VARS} to a Hugging Face token that has accepted "
            "pyannote/speaker-diarization-3.1's terms of use"
        )

    wav_path = tmp_path / "speech.wav"
    _synthesize_speech_wav(wav_path)

    engine = PyannoteSpeakerDiarizationEngine(hf_token=token)
    result = engine.diarize(wav_path)

    assert result.model_name == "pyannote/speaker-diarization-3.1"
    assert result.speaker_count >= 1
    assert result.regions, "expected at least one detected speech region"
