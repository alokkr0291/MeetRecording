# LICENSES.md — Dependency and Asset License Inventory

Project license: proprietary, all rights reserved (placeholder until a commercial license is chosen).

Review this file before any distribution. Obligations column states what we must do to comply.

## Python runtime dependencies

| Package | License | Obligations for commercial distribution |
|---|---|---|
| PySide6 / shiboken6 | LGPL-3.0 (commercial license also available from Qt Company) | Keep Qt dynamically linked (PyInstaller onedir does this); ship license text; allow users to replace the Qt libraries; do not statically link. Do not modify Qt sources, or publish modifications if we do. |
| pydantic, pydantic-settings, pydantic-core | MIT | Include license text |
| typing-extensions | PSF-2.0 | Include license text |
| annotated-types | MIT | Include license text |

## Development-only dependencies (not distributed)

| Package | License |
|---|---|
| pytest, pytest-timeout | MIT |
| ruff | MIT |
| mypy | MIT |
| PyInstaller | GPL-2.0 with bootloader exception (the exception allows distributing packaged apps under any license) |

## Phase 4+ (planned, not yet added)

| Component | License | Notes |
|---|---|---|
| faster-whisper | MIT | |
| CTranslate2 | MIT | |
| Whisper model weights (Systran/faster-whisper-* on Hugging Face) | MIT | Downloaded by the user on first use after confirmation; not bundled |
| Ollama (runtime) | MIT | Not bundled; user installs separately |
| LLM weights pulled via Ollama | model-specific (e.g. Llama community license, Apache-2.0 for Qwen/Mistral variants) | User chooses model; show license link before pull |

## Native and system components

| Component | License | Notes |
|---|---|---|
| ScreenCaptureKit, AVFoundation, CoreMedia (macOS frameworks) | Apple SDK license | Linked dynamically from the OS; permitted for distributed apps |
| Swift standard library | Apache-2.0 with runtime library exception | Statically embedded or from OS; no obligations beyond notice |
| FFmpeg (Homebrew build 7.1.1) | LGPL-2.1+ core; the Homebrew bottle is built with `--enable-gpl` (libx264) which makes that binary GPL-2.0+ | Do not bundle the Homebrew binary in a proprietary app. Options: (a) require users to install FFmpeg themselves (current dev approach), (b) build an LGPL-only FFmpeg without libx264/GPL components, (c) replace post-processing with AVFoundation in the helper. Decide before Phase 6. |

## Assets

| Asset | Source | License |
|---|---|---|
| Menu-bar icons | Generated programmatically in `ui/icons.py` (QPainter) | Project |
| Test tone | Generated at test time | n/a |

## Review log

| Date | Reviewer | Outcome |
|---|---|---|
| 2026-10-15 | initial | Inventory created; FFmpeg bundling flagged as a Phase 6 decision |
