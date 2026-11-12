# LocalMeetingRecorder

A native-feeling macOS menu-bar application that records meetings locally — capturing screen video, system audio (participants), and microphone audio — regardless of the conferencing application in use.

> **Status:** Recording, local/cloud transcription, speaker diarization, and AI summaries
> are implemented; CPU-first/low-resource support (Phase 6B) and Windows support (Phase 7,
> not started) are described below — see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)

---

## Key Features

- **Universal recording** — works with Teams, Zoom, Google Meet, Slack, and any other app
- **Menu-bar UI** — unobtrusive; Start/Stop with a single click
- **Two-track audio** — system audio (incoming) and microphone (outgoing) in separate tracks
- **Local-first** — no cloud upload, no external services by default
- **Transcription** — local speech-to-text with `faster-whisper` (CPU only, no GPU
  required or used), or an optional cloud provider (OpenAI, OpenRouter) — runs
  automatically after every recording
- **Speaker identification** — optional, local, CPU-only diarization via `pyannote.audio`
  (needs a free Hugging Face token; off by default — see Hardware & Performance below)
- **AI summaries** — action items and decisions via local Ollama or an optional cloud
  provider, exported as both structured JSON and a readable `summary.md`
- **CPU-first by design** — no NVIDIA GPU, no CUDA installation, and no GPU drivers are
  ever required for any feature; see Hardware & Performance

## Platform

| Platform | Status |
|---|---|
| macOS 15.0+ (arm64 / Intel) | Active development |
| Windows 10/11 | Planned (Phase 7) — no capture code exists yet; see PLAN.md's Phase 7 gap analysis |

## Requirements

- macOS 15.0 (Sequoia) or later — required for `SCStreamConfiguration.captureMicrophone`
- Python 3.13+
- FFmpeg (for post-processing and validation)
- Swift Command Line Tools (for building the capture helper — no Xcode needed)
- **No GPU, no CUDA, no NVIDIA drivers of any kind** — every AI feature runs on the CPU
  by explicit configuration, never auto-detected (see Hardware & Performance below)

## Hardware & Performance

Recording itself has modest requirements (the same as any screen recorder); local AI
processing (transcription, optional diarization, optional local summarization) is the
part that scales with your CPU and RAM. **A GPU is never required for anything in this
app** — `faster-whisper`/`ctranslate2` and `pyannote.audio`/`torch` are both explicitly
configured for `device="cpu"` in this app's code, regardless of what hardware is
available; nothing auto-detects or silently prefers a GPU.

| Tier | CPU | RAM | What you get |
|---|---|---|---|
| Minimum | Any Mac meeting the macOS 15.0 requirement above (entry-level Apple Silicon or an older Intel Mac) | 8 GB | Recording + local transcription with the **Low Resource** processing profile (Whisper "tiny", one AI job at a time, diarization off) |
| Recommended | A recent Apple Silicon Mac (M1 or newer) | 16 GB | The **Balanced** profile (default): Whisper "base", diarization available if you enable it |
| Advanced | A fast multi-core Mac (M-series Pro/Max, or a fast Intel Mac) | 32 GB+ | The **Quality** profile: larger Whisper models ("small" and up), diarization, optional cloud AI — local AI processing is still CPU-only and will take noticeably longer than on a GPU-accelerated service |

Pick (or let the app pick) a **Processing Profile** in Settings → General:

- **Low Resource** — smallest Whisper model, at most one AI job running at a time
  app-wide, speaker identification off. For older or entry-level machines.
- **Balanced** (default) — a sensible middle ground for most computers.
- **Quality** — larger models and diarization/cloud AI stay available; still CPU-only,
  but noticeably slower on an older CPU.

Changing the profile applies a recommended Whisper model immediately; you can still
pick a different model afterward. See IMPLEMENTATION_STATUS.md's Phase 6B section and
`scripts/benchmark_cpu.py` for real (not estimated) measured timings on the hardware
this project has actually been tested on, and to reproduce them on yours.

Speaker identification (`pyannote.audio`) is a separate, optional install (the
`diarization` extra) — it is **not** included in the default packaged `.app` download,
specifically to keep that download small for the majority of users who don't enable it.
On Windows or Linux, install it with CPU-only PyTorch wheels via
`scripts/install_diarization_cpu.sh` (or `.ps1`) rather than a plain `pip install`, which
would otherwise pull PyTorch's multi-gigabyte CUDA build by default on those platforms
(macOS has no such problem — its PyPI wheels are CPU/MPS-only already).

## Quick Start (Development)

```bash
# 1. Clone / enter project
cd local-meeting-recorder

# 2. Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Add the `transcription` extra for faster-whisper (Phase 4); it is optional because
# ctranslate2/faster-whisper are a heavy download not needed for recording alone:
pip install -e ".[dev,transcription]"

# 3. Build native capture helper (requires Swift CLT)
bash scripts/build_helper.sh

# 4. Run linting and unit tests
bash scripts/test.sh

# 5. Launch application
bash scripts/run_dev.sh
# OR in PyCharm: set interpreter to .venv, run src/local_meeting_recorder/main.py
```

### Granting permissions during development

`scripts/run_dev.sh` runs the app as a bare interpreter process, so Screen Recording /
Microphone prompts get attributed to whatever launched it (Terminal, PyCharm) — and on
a Homebrew "framework" Python install, the interpreter unconditionally relaunches itself
through its own bundled `Resources/Python.app` stub (this happens for *any* script, not
just this one), so you may see **"Python"** rather than your terminal in the list.

For a cleaner identity, build and launch the small dev `.app` wrapper instead:

```bash
bash scripts/build_dev_app.sh
open packaging/macos/dev/LocalMeetingRecorder.app
```

This is not the Phase 6 packaged build (no PyInstaller, no code signing/notarization) —
just an `Info.plist` + a launcher script + a generated icon, so macOS has a named,
iconned bundle to attribute the permission prompt to. Look for **"LocalMeetingRecorder"**
in System Settings → Privacy & Security → Screen Recording / Microphone first; if it
still isn't listed, look for **"Python"** instead (see above) — either one grants the
same underlying process tree access. Rebuild the wrapper after pulling code changes;
rerun `scripts/build_dev_app.sh` any time the icon or `Info.plist` needs regenerating.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design. In brief:

```
PySide6 menu-bar UI
    └── RecordingCoordinator (state machine)
            └── MacOSCaptureBackend (Python adapter)
                    └── lmr-capture-helper (Swift, ScreenCaptureKit + AVAssetWriter)
                            └── recording.mov  (1 video + 2 audio tracks)
```

## Permissions Required

- **Screen Recording** — to capture display frames and system audio
- **Microphone** — to capture your voice

Both are requested at runtime with a clear explanation. All data stays on your Mac.

## Privacy

Recording never starts silently. Every recording session requires explicit user action. See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

## License

Proprietary — all rights reserved. See [LICENSES.md](LICENSES.md) for third-party dependency licenses.

## Documentation

| Document | Purpose |
|---|---|
| [PLAN.md](PLAN.md) | Requirements, roadmap, and acceptance criteria |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design and layer responsibilities |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Phase-by-phase progress tracker |
| [TESTING.md](TESTING.md) | Test strategy and manual validation checklist |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | Privacy commitments and threat model |
| [LICENSES.md](LICENSES.md) | Dependency license inventory |
| [CHANGELOG.md](CHANGELOG.md) | Change history |
| [CLAUDE.md](CLAUDE.md) | AI agent context for continuation sessions |
