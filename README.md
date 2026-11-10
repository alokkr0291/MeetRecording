# LocalMeetingRecorder

A native-feeling macOS menu-bar application that records meetings locally — capturing screen video, system audio (participants), and microphone audio — regardless of the conferencing application in use.

> **Status:** Milestone 1 in progress — see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)

---

## Key Features

- **Universal recording** — works with Teams, Zoom, Google Meet, Slack, and any other app
- **Menu-bar UI** — unobtrusive; Start/Stop with a single click
- **Two-track audio** — system audio (incoming) and microphone (outgoing) in separate tracks
- **Local-first** — no cloud upload, no external services by default
- **Transcription** — local speech-to-text with `faster-whisper` (Phase 4)
- **AI summaries** — action items and decisions via local Ollama (Phase 5)
- **Searchable library** — full-text search across transcripts (Phase 4)

## Platform

| Platform | Status |
|---|---|
| macOS 15.0+ (arm64 / Intel) | Active development |
| Windows 10/11 | Planned (Phase 7) |

## Requirements

- macOS 15.0 (Sequoia) or later — required for `SCStreamConfiguration.captureMicrophone`
- Python 3.13+
- FFmpeg (for post-processing and validation)
- Swift Command Line Tools (for building the capture helper — no Xcode needed)

## Quick Start (Development)

```bash
# 1. Clone / enter project
cd local-meeting-recorder

# 2. Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

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
