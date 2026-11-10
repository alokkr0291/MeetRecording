# IMPLEMENTATION_STATUS.md

Source of truth for progress across sessions. Update at the end of every working session.

Last updated: 2026-11-04 (session 5)

Legend: `DONE` = implemented and verified as described; `PARTIAL` = implemented, verification incomplete; `TODO` = not started; `BLOCKED` = cannot proceed without external input.

---

## Phase 0 — Environment + Governance

| Task | Status | Notes |
|---|---|---|
| Environment inspection | DONE | macOS 26.2 arm64, Python 3.13.6 (Homebrew), Swift 6.2 CLT (no Xcode), FFmpeg 7.1.1, Ollama 0.12.6 (not running), Jabra SPEAK 410 default in/out |
| Directory structure | DONE | |
| README.md, PLAN.md, ARCHITECTURE.md | DONE | |
| TESTING.md, SECURITY_AND_PRIVACY.md, LICENSES.md, CHANGELOG.md, CLAUDE.md | DONE | |
| .claude/ memory + skills | DONE | |
| .gitignore, .env.example | DONE | |

## Phase 1 — Python Foundation

| Task | Status |
|---|---|
| pyproject.toml (ruff, mypy, pytest) | DONE |
| .venv + editable install | DONE |
| config.py (pydantic-settings) | DONE |
| infrastructure/logging_setup.py | DONE |
| infrastructure/subprocess_manager.py | DONE |
| domain/ (session id, state machine, metadata) | DONE |
| storage/paths.py, storage/session_store.py | DONE |
| Unit tests green (ruff + mypy + pytest) | DONE — 65 tests |

## Phase 2 — Verified Capture

| Task | Status | Notes |
|---|---|---|
| Swift helper source + Info.plist + build script | DONE | `native/macos/Sources/main.swift`, 950 lines |
| Helper builds with swiftc | DONE | rebuilt and verified this session: `probe`/`list-displays` respond correctly |
| capture/protocols.py | DONE | typed wire models matching the real helper protocol (not the earlier ARCHITECTURE.md sketch — see native/macos/PROTOCOL.md for corrections found this session) |
| capture/fake.py (fake backend) | DONE | |
| capture/windows/backend.py (stub) | TODO | Phase 7, intentionally deferred |
| capture/macos/helper_client.py + backend.py | DONE | |
| media/inspector.py | DONE | ffprobe stream/format summary + ffmpeg `astats` RMS |
| media/postprocess.py | TODO | mixdown/16kHz WAV extraction — not needed until Phase 4 transcription |
| Protocol tests with fake helper | DONE | `tests/unit/capture/` — 32 tests against a scripted fake helper process (startup, probe, displays, start/stop, invalid JSON, crash, timeout, SIGTERM/SIGKILL escalation, cleanup) |
| Media inspector integration tests | DONE | `tests/integration/media/` — 5 tests against real ffmpeg-synthesized media (stream shape, RMS on tone vs. silence, error paths) |
| Hardware acceptance test written | DONE | `tests/capture/test_real_capture.py`, gated by `LMR_HW_TESTS=1` |
| Hardware acceptance test executed on this Mac | BLOCKED | see below |

### Hardware verification results

Run 2026-11-04 with `LMR_HW_TESTS=1 pytest tests/capture -s -q`: **skipped**, not passed.
`backend.check_permissions()` ran successfully against the real helper binary (proving the
probe round-trip works end to end), and reported Screen Recording = `denied`, Microphone =
`not_determined`. The test honestly skipped with that exact reason rather than faking a
result.

Root cause: this session runs inside the Claude Code CLI's own subprocess (`ps` shows the
responsible/parent process is `claude`, not Terminal.app or PyCharm), which has never been
granted Screen Recording, and TCC permission is attributed to that responsible process, not
to `lmr-capture-helper`. Granting Screen Recording to the agent's own CLI process is a
real, slightly unusual security decision — not one to make unilaterally — so this session
did not attempt to trigger or push through the permission prompt.

**Action needed from a human:** run `bash scripts/build_helper.sh` then
`LMR_HW_TESTS=1 pytest tests/capture -s -q` from a terminal (Terminal.app, iTerm, or
PyCharm's run console) that you are willing to grant Screen Recording + Microphone to via
System Settings → Privacy & Security. The full recording path (ScreenCaptureKit video,
system audio, and microphone, end to end through `MacOSCaptureBackend`) remains unverified
against real hardware until that run happens.

## Phase 3 — Application Shell

| Task | Status | Notes |
|---|---|---|
| RecordingCoordinator + tests | DONE | `application/recording_coordinator.py`; 25 tests in `tests/unit/application/` (`test_recording_coordinator.py`, `test_recovery.py`) |
| Session manager + recovery | DONE | No separate `session_manager.py`: `storage/session_store.py` (Phase 1) plus `domain/session_id.py` already covered session creation/persistence, so the coordinator composes them directly rather than adding a thin pass-through wrapper. `application/recovery.py` added for incomplete-session detection. |
| Tray UI, consent dialog, permission guide, settings | DONE | `ui/tray.py`, `ui/consent_dialog.py`, `ui/permission_guide.py` (settings dialog itself still TODO — not blocking a usable app; all settings have workable defaults in `config.py`) |
| app.py / main.py | DONE | `app.py` (`Application` composition root), `main.py` (entry point, settings/logging/single-instance guard) |
| Manual launch from PyCharm | PARTIAL | Launches and runs correctly via `python -m local_meeting_recorder.main` in this environment (verified: tray shows, Start/Stop wired, shuts down cleanly — see Phase 3b notes); not yet launched from an actual PyCharm run configuration by a human |

### Phase 3a notes

- `RecordingCoordinator` depends only on `capture.protocols.CaptureBackend`; verified
  against `FakeCaptureBackend` only (`MacOSCaptureBackend` wiring is exercised by
  construction, not by a real recording — that still needs the hardware test above).
- Backend callback wiring: a `CaptureBackend`'s `on_stats`/`on_warning`/`on_crash` are
  fixed at its own construction time (Phase 2b, intentionally untouched), so
  `RecordingCoordinator` takes a `backend_factory` callable rather than a pre-built
  backend — see the module docstring in `recording_coordinator.py` for the exact reason
  and an example.
- One small additive change to Phase 2b: `FakeCaptureBackend.start()` now touches the
  output file (0 bytes) when a recording "starts," mirroring what `AVAssetWriter` does in
  the real helper. No existing Phase 2b test depended on the file's absence; this was
  needed so the coordinator's "verify the output file exists before marking Saved" check
  is meaningfully exercised against the fake backend.

### Phase 3b notes (desktop UI)

- **Framework:** PySide6 (`QSystemTrayIcon`/`QMenu`), as already set in `pyproject.toml`
  and `ARCHITECTURE.md` — no framework migration.
- **New modules:** `ui/tray.py` (`TrayController`, pure presentation), `ui/coordinator_bridge.py`
  (`CoordinatorBridge`, marshals the coordinator's blocking calls onto background threads
  and re-emits results as Qt signals), `ui/app_controller.py` (`AppController`, the one
  place that knows the UI flow — check permissions, then consent, then start), `ui/consent_dialog.py`,
  `ui/permission_guide.py`, `ui/shell.py` (Finder/System Settings `open`), `ui/icons.py`
  (painted dots, no bundled image assets), `app.py`, `main.py`,
  `infrastructure/single_instance.py`, `capture/factory.py` (new: the `sys.platform`
  dispatch the architecture skill already described but that didn't exist yet).
- **One small additive change to Phase 3a:** `RecordingCoordinator.check_permissions()` —
  a passthrough to `backend.check_permissions()` with no state-machine side effects, added
  because the UI needs to show permission status before offering the consent dialog, which
  nothing in Phase 3a previously exposed.
- **Verified by real (non-mocked) smoke test, twice:** constructed the real `Application`
  with a real `MacOSCaptureBackend` (via `capture.factory.default_backend_factory()`) and
  the real helper binary, auto-quitting after ~1.5 s — tray appeared, no exceptions, clean
  shutdown with `exec()` returning 0 both as a standalone script and through
  `main.py:_run()`. This proves the whole wiring works up to the point of actually
  recording; Start/Stop against ScreenCaptureKit itself is still blocked on the same
  permission issue as Phase 2/3a (see "Hardware verification results" above — unchanged in
  this session, still needs a human to run it from a terminal they'll grant permissions to).
- 46 new tests, all green, 0 flaky across 5 repeated full-suite runs (one real race was
  found and fixed during this session: a test polled `coordinator.state` directly and then
  read tray text without re-polling, which can observe the state change before the
  corresponding queued cross-thread Qt signal has actually been delivered to the tray —
  fixed by polling the tray's own text instead).

### Dev-mode app identity (session 5)

User couldn't find the app in System Settings to grant Screen Recording. Added
`scripts/make_app_icon.py` (renders a painted icon via QPainter + `iconutil`, no bundled
assets) and `scripts/build_dev_app.sh` (builds `packaging/macos/dev/LocalMeetingRecorder.app`
— a minimal Info.plist + launcher script + icon; explicitly **not** the Phase 6 packaged
build, no PyInstaller/signing/notarization). Verified the bundle, icon, and ad-hoc
codesign all come out correct, and that `open`ing it does launch the real app.

However: launching via the wrapper does not fully solve the identity problem on this
machine. Homebrew's framework Python build relaunches *every* invocation (verified with a
plain `python3 -c "..."`, unrelated to this app) through its own bundled
`Resources/Python.app`, so the running process ends up outside the wrapper regardless of
how it was started — see decision D12 in `.claude/project-memory.md`. README.md and
CLAUDE.md now document looking for "Python" as a fallback. The fully reliable fix is a
frozen Phase 6 build, where the process never leaves the real app bundle; this is an open
question for the user (pull Phase 6 forward, or accept the "Python" identity for now).

## Phases 4–7

Not started. See PLAN.md.

## Known limitations and blockers

- Hardware acceptance test cannot complete inside this agent session because the
  responsible process for TCC permission (the Claude Code CLI itself) has not been granted
  Screen Recording. See "Hardware verification results" above. Not a code defect.
- `ARCHITECTURE.md`'s original helper-protocol sketch (written before the Swift helper
  existed) had drifted from the real implementation in several ways — field names
  (`capture_mic` vs. the real `capture_microphone`), a `session_id` that was never actually
  part of the wire protocol, an oversimplified `stats` shape, and two undocumented commands
  (`ping`, `request-permissions`). Corrected in `native/macos/PROTOCOL.md` (new) and
  `ARCHITECTURE.md`; `capture/protocols.py` models the real shape only.
