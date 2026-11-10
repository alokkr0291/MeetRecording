# Project memory — LocalMeetingRecorder

Durable facts and decisions. Append; do not rewrite history. Newest entries at the bottom of each section.

## Environment (verified 2026-10-15)

- Machine: Mac Studio M2, arm64, macOS 26.2 (build 25C56). Two displays: 1920x1080 (main) and 1080x1920.
- Python: `/opt/homebrew/bin/python3` = 3.13.6. Also present: Anaconda (`/opt/anaconda3`), `uv 0.9.26`, `poetry`. Project uses a local `.venv` from Homebrew Python. Do not install into global or conda environments.
- Swift: 6.2 from Command Line Tools at `/Library/Developer/CommandLineTools`, SDK 26.0. `xcodebuild` is not available and is not needed; `swiftc` builds the helper.
- FFmpeg 7.1.1 (Homebrew) with `h264_videotoolbox`, `aac`, `libopus`, `libx264` (GPL build; do not bundle).
- Audio devices: Jabra SPEAK 410 USB (default input and output), Mac Studio Speakers, HA270, HDMI, Microsoft Teams Audio (virtual), ZoomAudioDevice (virtual). No BlackHole.
- Ollama 0.12.6 installed at `/usr/local/bin/ollama`, not running by default. Models present: qwen3-coder:30b, llava, nomic-embed-text. No general chat model; pulling one needs user approval.
- PyCharm at `/Applications/PyCharm.app`.
- Free disk: ~180 GB.

## Decisions

- D1 Capture via Swift helper using ScreenCaptureKit (`capturesAudio` + `captureMicrophone`), not pyobjc, not FFmpeg avfoundation. Reason: system audio is only reachable through SCK without a kernel/virtual audio driver; pyobjc sample-buffer callbacks are fragile and hard to package.
- D2 Encoding in the helper with AVAssetWriter: 1 H.264 video track + 2 AAC audio tracks (track order: system, microphone) in a `.mov`. FFmpeg only for post-processing and verification.
- D3 IPC is JSON lines over stdin/stdout; helper exits on stdin EOF. Documented in `native/macos/PROTOCOL.md`.
- D4 Minimum macOS 15.0 (SCK microphone capture). Deployment target set in the build script.
- D5 State machine: Idle, Preparing, Recording, Finalizing, Saved, Error. Saved is a transient reporting state that returns to Idle.
- D6 Settings via pydantic-settings, `LMR_` prefix, optional TOML at the app-support root.
- D7 Transcription with faster-whisper (Phase 4); summaries with Ollama HTTP (Phase 5).
- D8 Packaging with PyInstaller onedir `.app`; helper under `Contents/Helpers/`.
- D9 FFmpeg is a runtime requirement found on PATH during development; bundling decision deferred to Phase 6 because the Homebrew build is GPL.
- D10 Capture layer: one `HelperClient` per logical operation, not reused across a recording. Reason: the real helper process exits on its own once a recording is stopped (confirmed by reading `main.swift`'s `onFinished`/`exit` path), so `MacOSCaptureBackend` spawns a fresh client for a permission/display query and another for each start-recording/stop-recording cycle, rather than trying to keep one process alive across operations.
- D11 Protocol docs were wrong. `ARCHITECTURE.md`'s original helper-protocol sketch (written before the Swift helper existed) does not match `native/macos/Sources/main.swift`: `capture_mic` should be `capture_microphone`, there is no `session_id` anywhere in the wire protocol, `stats`/`stopped` carry many more fields than sketched, and `ping` / `request-permissions` were undocumented. Fixed in `native/macos/PROTOCOL.md` (new file — it was referenced by two docs but did not exist) and in `ARCHITECTURE.md`.
- D12 Homebrew's `python@3.13` "framework" build unconditionally relaunches every invocation of `bin/python3.13` through its own bundled `Resources/Python.app/Contents/MacOS/Python` stub (confirmed via `__PYVENV_LAUNCHER__` string in the binary and by observing it happen for a bare `python3 -c "..."` with no GUI code at all). This means a dev-mode TCC permission prompt may be attributed to "Python" even when launched through a custom `.app` wrapper (`scripts/build_dev_app.sh`), because the running process image changes away from the wrapper before any Screen Recording/Microphone API is touched. Not fixable from application code; the durable fix is a frozen Phase 6 build (PyInstaller bundles its own runtime and never relaunches out of the app bundle).

## Open questions for the user

- Apple Developer account for signing/notarization (needed in Phase 6).
- Which Ollama chat model to pull for summaries (Phase 5).
- Commercial license text for the project itself.
- The hardware acceptance test (`tests/capture/test_real_capture.py`) cannot be completed from an agent session: this shell's TCC-responsible process is the Claude Code CLI itself (verified via `ps`), which has not been granted Screen Recording, and granting it is a real permission decision that should be made deliberately, not by an agent. Run `LMR_HW_TESTS=1 pytest tests/capture -s -q` from a terminal/IDE you're willing to grant Screen Recording + Microphone to.
- Whether granting permission to "Python" (D12) is acceptable for development, or whether Phase 6 packaging should be pulled forward so permissions attach to "LocalMeetingRecorder" specifically — needs the user to try both and report what they see in System Settings on their machine (this agent's sandboxed shell cannot complete an interactive TCC grant to check).

## Session log

- 2026-10-15 session 1: environment inspected, plan approved, scaffold and governance docs created, Milestone 1 implementation started.
- 2026-11-04 session 2: Phase 1 confirmed complete (65 tests green — docs had gone stale). Implemented Phase 2b (`capture/protocols.py`, `capture/macos/helper_client.py`, `capture/macos/backend.py`, `capture/fake.py`), `media/inspector.py` (Phase 2c, inspector only), and the Phase 2d hardware acceptance test. 37 new tests (32 unit against a fake helper process, 5 integration against real ffmpeg-synthesized media), all green, plus the existing 65. Found and fixed protocol-documentation drift (D11). Hardware test correctly skips in this environment (see open questions) — full real-capture verification is still outstanding and needs a human to run it.
- 2026-11-04 session 3: implemented Phase 3a (`application/recording_coordinator.py`, `application/recovery.py`). 25 new tests (92 total unit+integration before this session's Phase 3b work), all green. One small additive Phase 2b touch: `FakeCaptureBackend.start()` now touches the output file.
- 2026-11-04 session 4: implemented Phase 3b (first usable desktop app): `ui/tray.py`, `ui/coordinator_bridge.py`, `ui/app_controller.py`, `ui/consent_dialog.py`, `ui/permission_guide.py`, `ui/shell.py`, `ui/icons.py`, `app.py`, `main.py`, `infrastructure/single_instance.py`, `capture/factory.py`. One additive Phase 3a touch: `RecordingCoordinator.check_permissions()`. 46 new tests (173 total), green across 5 repeated runs. Verified with a real (non-mocked) `MacOSCaptureBackend` smoke test. Found and fixed one real test race (polling `coordinator.state` then reading tray text without re-polling can observe the state before the queued cross-thread signal is delivered).
- 2026-11-04 session 5: user reported they could not find the app in System Settings to grant Screen Recording. Built `scripts/make_app_icon.py` + `scripts/build_dev_app.sh` (a minimal, non-Phase-6 `.app` wrapper with a generated icon, for TCC identity only) and discovered D12 while verifying it actually works. Documented the "Python" fallback in README.md/CLAUDE.md since the wrapper alone does not fully solve the identity problem on this machine's Python install.
