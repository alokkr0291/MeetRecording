#!/usr/bin/env bash
# Installs the optional "diarization" extra with CPU-only PyTorch wheels (Phase 6).
#
# Why this script exists: PyPI's default `torch`/`torchaudio` wheel on Linux (and
# Windows, once a native capture backend exists there — see PLAN.md Phase 7) is the
# CUDA-enabled build, which pulls multi-gigabyte NVIDIA CUDA/cuDNN runtime libraries
# even on a machine with no GPU at all. This app never needs or uses a GPU for either
# Whisper or pyannote (both are pinned to device="cpu" explicitly — see
# transcription/engine.py / speaker/pyannote_engine.py), so there is no reason to ever
# download the CUDA build here. On macOS this script is a no-op beyond a plain install:
# PyPI's macOS wheels are CPU/MPS-only already, there is no separate CUDA variant to
# avoid.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "==> installing CPU-only torch/torchaudio"
python -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0" "torchaudio==2.6.0"

echo "==> installing the diarization extra (pyannote.audio and the rest)"
python -m pip install -e ".[diarization]"

echo "==> done — speaker identification is available once a Hugging Face token is added in Settings"
