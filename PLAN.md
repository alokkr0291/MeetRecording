# PLAN.md — LocalMeetingRecorder

## 1. Product Requirements

### Core Capture Requirements (must work in Milestone 1)
- Capture display video from a user-selected display
- Capture system audio (all audio playing through macOS output — meeting participants)
- Capture microphone audio (outgoing voice)
- All three sources time-synchronized under a single clock (SCK host time)
- Output: `.mov` container, 1 H.264 video track + 2 AAC audio tracks (system, mic)

### Application Requirements
- macOS menu-bar application (LSUIElement, no Dock icon)
- Recording states: Idle → Preparing → Recording → Finalizing → Saved | Error
- Elapsed timer in menu while recording
- Explicit Start / Stop controls — no auto-start
- Recording history with local file management
- Local speech-to-text transcription (Phase 4)
- Searchable transcripts with timestamps (Phase 4)
- AI-generated summaries, decisions, action items (Phase 5)
- No cloud data transmission by default

### Non-Requirements (deferred)
- iOS / iPadOS recording
- Remote/cloud transcription (optional future feature)
- Windows (Phase 7 — architecture supports it)

### Acceptance Criteria — Milestone 1 (Phases 0–3)

| # | Criterion | Verification |
|---|---|---|
| M1-01 | App launches as menu-bar item, no Dock icon | Manual launch |
| M1-02 | Start/Stop accessible from menu | Manual |
| M1-03 | State transitions are exclusive and sequential | Unit test |
| M1-04 | Consent dialog shown before every recording | Manual |
| M1-05 | Screen Recording permission requested and actionable if denied | Manual |
| M1-06 | Microphone permission requested and actionable if denied | Manual |
| M1-07 | Recording produces a `.mov` with 1 video + 2 audio tracks | `ffprobe` |
| M1-08 | System audio track contains actual meeting audio | RMS + freq test |
| M1-09 | Microphone track contains actual mic audio | RMS test |
| M1-10 | A/V sync error < 100 ms at start | `astats` onset analysis |
| M1-11 | Helper process fully exits after Stop | `pgrep` check |
| M1-12 | Incomplete session detected and offered recovery on next launch | Unit + manual |
| M1-13 | Recording preserved when transcription fails | Integration test |
| M1-14 | Unit tests pass: `ruff`, `mypy`, `pytest tests/unit` | CI run |

---

## 2. Architecture Summary

See [ARCHITECTURE.md](ARCHITECTURE.md) for full diagrams. Layers:

1. **Presentation** — PySide6 `QSystemTrayIcon`, consent dialog, settings
2. **Application** — `RecordingCoordinator` state machine, session lifecycle
3. **Capture** — Python protocols + macOS adapter + Swift helper subprocess
4. **Media** — AVAssetWriter (in Swift), FFmpeg post-processing, file inspector
5. **Persistence** — session directories, `session.json`, SQLite + FTS5 (Phase 4)
6. **Transcription** — `faster-whisper` (Phase 4)
7. **Summarization** — Ollama HTTP client (Phase 5)
8. **Infrastructure** — settings, logging, subprocess manager, diagnostics

---

## 3. Phased Roadmap

### Phase 0 — Environment + Governance [DONE]
- Environment inspection, dependency audit, capture API validation
- Create all governance documents and `.claude/` context
- **Done when:** all documents exist and reflect actual environment findings

### Phase 1 — Python Foundation [IN PROGRESS]
- `pyproject.toml` with ruff, mypy, pytest
- Project-local `.venv` (Homebrew Python 3.13)
- `config.py` — typed settings via `pydantic-settings` with `LMR_` prefix
- `infrastructure/logging_setup.py` — structured JSON logging
- `domain/` — `SessionId`, `RecordingState` enum, state transitions, `SessionMetadata`
- `storage/paths.py` — Application Support paths
- `infrastructure/subprocess_manager.py` — bounded subprocess lifecycle
- Unit tests for all domain logic
- **Done when:** `ruff check .`, `mypy src/`, `pytest tests/unit/` all pass

### Phase 2 — Verified Capture [CORE MILESTONE]
**Phase 2a — Swift Helper:**
- `native/macos/Sources/main.swift` — ScreenCaptureKit + AVAssetWriter
  - `SCStreamConfiguration`: `capturesAudio=true`, `captureMicrophone=true`
  - `AVAssetWriter` with 1 `AVVideoCompressionProperties` H.264/VideoToolbox video track
  - 2 AAC audio tracks (system=track 0, mic=track 1) sharing SCK clock
  - Embedded `Info.plist` via linker (`-sectcreate __TEXT __info_plist`)
  - JSON-lines protocol on stdin/stdout
- `scripts/build_helper.sh` — `swiftc` build, no Xcode required
- **Done when:** helper binary responds to `probe` and `list-displays`

**Phase 2b — Python Bridge:**
- `capture/protocols.py` — `CaptureBackend`, `PermissionStatus`, `DisplayInfo` protocols
- `capture/macos/helper_client.py` — `HelperClient` (Popen, watchdog, reader thread)
- `capture/macos/backend.py` — `MacOSCaptureBackend`
- `capture/windows/backend.py` — `UnsupportedPlatformBackend` (raises `NotImplementedError`)
- `capture/fake.py` — `FakeCaptureBackend` for coordinator tests
- Protocol tests using a fake helper process
- **Done when:** protocol tests pass, HelperClient handles crash + timeout

**Phase 2c — Media Inspector:**
- `media/inspector.py` — `ffprobe` stream metadata, per-track RMS via `astats`
- `media/postprocess.py` — mixdown MP4, 16 kHz WAV extraction for transcription
- **Done when:** inspector correctly identifies stream counts and RMS levels

**Phase 2d — Hardware Acceptance Test:**
- `tests/capture/test_real_capture.py` — gated by `LMR_HW_TESTS=1`
- Plays 440 Hz tone via `afplay` (through Jabra speaker)
- Records 8 s, checks: 1 video + 2 audio tracks, video frames > 0, system RMS > threshold, mic RMS > threshold (acoustic pickup via Jabra mic)
- **Done when:** test passes, or exact limitation documented with evidence

### Phase 3 — Application Shell
**Phase 3a — Coordinator [DONE]:**
- `application/recording_coordinator.py` — `RecordingCoordinator`: state machine
  (`domain.recording_state.RecordingState`, reused as-is), session ID generation, talks
  only to the `CaptureBackend` protocol (works with `MacOSCaptureBackend` and
  `FakeCaptureBackend` unchanged).
- Session dir: `~/Library/Application Support/LocalMeetingRecorder/recordings/<session_id>/`
- `session.json` — written at start with `status: in_progress`, updated at finalize
- `application/recovery.py` — incomplete session detection on launch. Implemented as
  "every session still `in_progress` when `recover_incomplete_sessions()` runs is
  incomplete" (via `SessionStore.find_incomplete()`, Phase 1) rather than the `>30 s mtime`
  heuristic sketched here originally: since recovery runs once, at coordinator
  construction, before any new recording can start, there is no live session that could
  be mistaken for an incomplete one — the mtime margin turned out to be unnecessary.
- Coordinator tests: dup start/stop, stop-when-idle, stop during Preparing (race against a
  slow `backend.start()`), permission denied, backend start failure, backend crash during
  recording (incl. a stale-crash-after-finalize non-double-handling case), stop
  failure/timeout, missing output file, shutdown, reset, recovered-session exposure.
- **Done when:** all coordinator unit tests pass — 25 tests, green with ruff/mypy/pytest.

**Phase 3b — Menu-bar UI [DONE]:**
- `ui/tray.py` — `TrayController`: `QSystemTrayIcon` with a dynamic menu (status, elapsed
  timer while Recording, Start/Stop enabled per `RecordingState`, Recent Recordings
  submenu, Open Recordings Folder, Quit). Pure presentation — no coordinator import.
- `ui/consent_dialog.py` — one popup, explicit OK, shown fresh every session (no
  "recording_indicator.py": the elapsed timer lives in the tray menu's status line
  instead of a separate visual indicator — simpler, and there is no main window to attach
  one to in a menu-bar-only app).
- `ui/permission_guide.py` — what's missing, a button per missing permission that opens
  the right System Settings pane (`x-apple.systempreferences:` deep links), and
  instructions to quit & reopen after granting.
- `ui/coordinator_bridge.py` — `CoordinatorBridge`: runs every coordinator call on a
  background thread, re-emits results/callbacks as Qt signals (safe queued cross-thread
  delivery onto the GUI thread).
- `ui/app_controller.py` — `AppController`: the UI flow (check permissions -> consent ->
  start; disable invalid actions; show errors/warnings/recovered sessions). Consent and
  permission-guide functions are injected, which is what makes this fully unit-testable
  without a real modal dialog.
- `ui/shell.py`, `ui/icons.py` — `open`-based Finder/System-Settings helper, painted tray
  dot icons (no bundled image assets to go stale).
- `app.py` — `Application`: composition root, owns the `QApplication` and the Qt event
  loop; `setQuitOnLastWindowClosed(False)` (required for a tray-only app — otherwise a
  transient dialog closing would be mistaken for "last window closed" and quit the app).
- `main.py` — entry point: settings, logging, `infrastructure/single_instance.py`
  (POSIX file-lock guard; Windows gets its own mechanism in Phase 7), platform backend
  via the new `capture/factory.py`.
- **Done when:** app launches, Start/Stop works, consent shown, timer visible — verified
  with a real (non-mocked) `MacOSCaptureBackend` + the built helper binary via a
  1.5 s-then-auto-quit smoke test: tray appears, no exceptions, clean shutdown. Actually
  recording through the UI is still blocked on the same Screen Recording permission issue
  as Phase 2d/3a.

### Phase 4 — Transcription + Library [FUTURE]
- SQLite schema + Alembic-style migrations
- `storage/database.py`, FTS5 index on transcript text
- `transcription/protocol.py`, `transcription/faster_whisper_backend.py`
- Job queue with progress events, retry, recording preserved on failure
- User prompted before model download

### Phase 5 — AI Summaries [FUTURE]
- `summarization/ollama_client.py`
- Structured summary schema (topics, decisions, action items, questions)
- Chunking + map-reduce aggregation for long meetings
- Prompt-injection hardening: transcript delimited, no tool use
- Retry if model unavailable

### Phase 6 — Packaging [FUTURE]
- PyInstaller spec with helper in `Contents/Helpers/`
- `Info.plist`: `LSUIElement`, `NSMicrophoneUsageDescription`, `NSScreenCaptureUsageDescription`
- Hardened runtime + `audio-input` entitlement
- Code signing + notarization documentation (requires Apple Developer account)
- Launch test on bare machine (outside PyCharm)

### Phase 7 — Windows [FUTURE]
- `capture/windows/backend.py` — Windows.Graphics.Capture + WASAPI loopback
- Same `CaptureBackend` protocol; Windows-specific media writing
- PyInstaller NSIS installer

---

## 4. Dependencies

### Runtime (Python)
| Package | Version | License | Purpose |
|---|---|---|---|
| PySide6 | ^6.8 | LGPLv3 | Menu-bar UI |
| pydantic-settings | ^2.3 | MIT | Typed config |
| pydantic | ^2.7 | MIT | Data models |

### Dev / Test
| Package | License | Purpose |
|---|---|---|
| pytest | MIT | Test runner |
| pytest-asyncio | Apache-2 | Async test support |
| ruff | MIT | Linter + formatter |
| mypy | MIT | Type checker |
| pytest-timeout | MIT | Test timeouts |

### Phase 4+
| Package | License | Purpose |
|---|---|---|
| faster-whisper | MIT | Local transcription |
| ctranslate2 | MIT | Inference engine |

### Native / External
| Tool | License | Purpose |
|---|---|---|
| Swift CLT (swiftc) | Apple proprietary | Build capture helper |
| ScreenCaptureKit | Apple proprietary | Display + audio capture |
| AVFoundation | Apple proprietary | Audio recording |
| FFmpeg | LGPL/GPL | Post-processing |
| Ollama | MIT | Local LLM runtime |

---

## 5. Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Capture engine | Swift SCK helper | Only reliable path for system audio on macOS; pyobjc SCK callbacks unreliable at production quality |
| Video encoding | H.264 VideoToolbox | Hardware-accelerated, widely compatible, available without GPU contention |
| Audio encoding | AAC via AVAssetWriter | Stays on SCK clock; no cross-process raw audio |
| IPC protocol | JSON lines on stdin/stdout | Simple, debuggable, language-agnostic; helper exits on stdin EOF |
| UI framework | PySide6 (LGPLv3) | Mature, well-documented Python Qt; arm64 wheels available |
| Settings | pydantic-settings | Type-safe, `.env` compatible, TOML-friendly |
| Logging | `structlog` / stdlib JSON | Structured, no PII, session-ID tagged |
| Transcription | faster-whisper | MIT, CPU int8, Python 3.13 wheel, no model auto-download |
| Summaries | Ollama HTTP | Local, configurable endpoint, no API key |
| Min macOS | 15.0 | Required for `captureMicrophone` on SCK |

---

## 6. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| TCC permission in dev vs packaged app | High | Document; test both; helper embeds Info.plist bundle ID |
| `swiftc` typecheck passes but runtime crashes | High | Run actual hardware tests before marking Phase 2 done |
| A/V sync drift | Medium | Single SCK clock for all tracks; onset analysis in acceptance test |
| Helper crash leaves orphan process | Medium | Watchdog + stdin-EOF self-termination |
| Disk full during recording | Medium | Free-space precheck; write-error event from helper; coordinator handles |
| Partial recording on app crash | Medium | `session.json` recovery; ffmpeg remux attempt |
| PySide6 LGPL obligation | Medium | Dynamic link only; document in LICENSES.md |
| Large model downloads | Low | User-confirmed download only; progress shown |
| Prompt injection via transcript | Low | Delimited prompts; no tool use in summarization |

---

## 7. Test Strategy

See [TESTING.md](TESTING.md) for full details.

- **Unit** — domain logic, state machine, config, paths (no I/O)
- **Integration** — storage, subprocess protocol, coordinator + fake backend
- **Capture (hardware-gated `LMR_HW_TESTS=1`)** — real capture acceptance tests
- **Packaging** — launch outside PyCharm, permissions in bundle

---

## 8. Packaging Strategy

1. **Development:** editable install from `.venv`, `python main.py`
2. **Packaged app:** PyInstaller `--windowed` + `--onedir`
   - Helper binary: `{bundle}/Contents/Helpers/lmr-capture-helper`
   - Resources: `{bundle}/Contents/Resources/`
   - Runtime data: `~/Library/Application Support/LocalMeetingRecorder/`
3. **Distribution:** notarized `.dmg` via `create-dmg`; requires Apple Developer account
4. **Models:** not bundled; downloaded on first use with user confirmation

---

## 9. Commercialization Considerations

Before commercial release, resolve:
- [ ] PySide6 LGPLv3 compliance (dynamic linking, license notice)
- [ ] FFmpeg license (current Homebrew build: LGPL; verify no GPL components activate for our use)
- [ ] Apple Developer Program membership for code signing + notarization
- [ ] Privacy policy: explicitly state no cloud upload
- [ ] Model weights licenses (Whisper: MIT; validate model source)
- [ ] App Store eligibility: SCK permission may require explicit entitlement review
- [ ] GDPR / CCPA: recordings may contain third-party voices; user consent requirement documented in consent dialog
- [ ] UI accessibility: VoiceOver support in menu and dialogs

---

## 10. Completion Criteria Summary

| Phase | Gate |
|---|---|
| 0 | All governance docs created and accurate |
| 1 | `ruff` + `mypy` + `pytest tests/unit` green |
| 2 | Hardware acceptance test passes (or exact limitation documented) |
| 3 | App launches, records, stops, shows consent, handles permissions |
| 4 | Transcription runs after finalization, recording preserved on failure |
| 5 | Summary generated from local model, retry on failure |
| 6 | Packaged app runs on bare machine with correct permissions |
| 7 | Windows capture works end-to-end on Windows 10/11 |
