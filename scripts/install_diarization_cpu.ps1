# Installs the optional "diarization" extra with CPU-only PyTorch wheels (Phase 6).
#
# Why this script exists: PyPI's default `torch`/`torchaudio` wheel on Windows is the
# CUDA-enabled build, which pulls multi-gigabyte NVIDIA CUDA/cuDNN runtime libraries
# even on a machine with no GPU at all. This app never needs or uses a GPU for either
# Whisper or pyannote (both are pinned to device="cpu" explicitly — see
# transcription/engine.py / speaker/pyannote_engine.py).
#
# Note: a native Windows capture backend does not exist yet (PLAN.md Phase 7) — this
# script only prepares the local-AI-processing dependencies, ahead of that landing.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}

Write-Host "==> installing CPU-only torch/torchaudio"
python -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0" "torchaudio==2.6.0"

Write-Host "==> installing the diarization extra (pyannote.audio and the rest)"
python -m pip install -e ".[diarization]"

Write-Host "==> done - speaker identification is available once a Hugging Face token is added in Settings"
