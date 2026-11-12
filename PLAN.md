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

### Phase 4 — Transcription [IMPLEMENTED, see IMPLEMENTATION_STATUS.md]
- `media/audio_extractor.py` — ffmpeg mixdown of every audio track to 16 kHz mono WAV
- `transcription/engine.py` — `faster-whisper`, lazy model load, explicit/auto language
- `transcription/models.py`, `transcription/store.py` — typed, validated, atomically
  written `transcript.json`/`transcript.txt` per session
- `transcription/manager.py` — one background thread per session, duplicate-run
  prevention, retry, recording preserved on failure (`post_processing.transcript_status`,
  never the session's own `status`)
- `ui/transcript_dialog.py`, `ui/transcription_bridge.py` — viewer + retry action, tray
  status suffix
- Deferred: SQLite/FTS5 cross-session search (a Phase 4 sketch originally, superseded by
  per-session file storage since nothing yet needs search across sessions — revisit if a
  library/search UI is scoped later), and a user-facing confirmation prompt before the
  first model download (currently downloads silently on first transcription; see
  IMPLEMENTATION_STATUS.md)

### Phase 4.5 — Application Settings and User Configuration [IMPLEMENTED, see IMPLEMENTATION_STATUS.md]
- `settings/models.py`, `settings/service.py` — typed, versioned `AppSettings`,
  atomically persisted to `settings.json` (separate from `config.py`'s env/`.env`/
  `config.toml` dev settings, and from credentials, which never live here)
- `infrastructure/credentials/` — provider-neutral `CredentialStore` protocol;
  `MacOSKeychainStore` via the `keyring` package; an explicit `UnavailableCredentialStore`
  for any platform with no secure store yet (never a silent plaintext fallback)
- `transcription/providers/` — `OpenAITranscriptionClient` and
  `OpenRouterTranscriptionClient`, both real and implemented (verified against each
  provider's published API reference first), behind a shared
  `TranscriptionProviderClient` protocol alongside local `TranscriptionEngine`; a cloud
  provider with no API key configured fails that one transcription job only, never the
  recording, never the app
- `storage/paths.py` — configurable recordings directory, validated before accepting;
  every previous location stays visible (`additional_recordings_dirs`) so a setting
  change never hides old recordings
- `ui/settings_dialog.py`, `ui/tray.py`'s "Settings…" item — General (storage
  location), Transcription (provider + model + masked API key + Test Connection),
  About; no accounts, no sign-in, no API key ever required for local recording

### Phase 5 — AI Meeting Intelligence, Model Selection and Speaker Attribution

Parts A+B [IMPLEMENTED, see IMPLEMENTATION_STATUS.md]:
- Part A: verified (required before any implementation) that the recording already
  captures system and microphone audio as genuinely separate tracks — confirmed against
  a real hardware-captured `.mov`, not just by reading the Swift source
- Part B: `media/audio_extractor.py::extract_track_wav16k_mono()` (isolate one track, no
  mixing), `transcription/models.py` (`AudioSource`, `TranscriptSegment.source`,
  `Transcript.source_errors`), `domain/session_metadata.py`
  (`TranscriptionStatus.PARTIAL`), `transcription/manager.py` (independent
  extraction+transcription per source, merged onto one timeline, one source's failure
  does not fail the other)

Parts C+E [IMPLEMENTED, see IMPLEMENTATION_STATUS.md Phases 5C/5E — session 10]:
- Part C: speaker **diarization** — `speaker/` (`pyannote/speaker-diarization-3.1` via
  `pyannote.audio`), distinguishes individual remote speakers within the system-audio
  track; the microphone track is always the fixed "You" identity, never diarized
- Objective 1: configurable AI models for meeting **summaries** — `summarization/`
  (Ollama/OpenAI/OpenRouter provider clients + structured schema), independent
  provider/model selection from the transcription settings
- Both were found already implemented but uncommitted and undocumented at the start of
  session 10; an engineering audit was run first, then the bugs/gaps it found were
  fixed (single-track diarization silently tagging nothing, a transcript write race
  between retry-transcription and diarization, no prompt-injection hardening despite
  this being an explicit original requirement below, no Ollama model-availability
  check, silent missing-API-key summary failures) — see IMPLEMENTATION_STATUS.md for
  the full list.
- **Not verified against real models/providers**: no Hugging Face token, local Ollama
  server, or cloud API key was available in the environment that implemented and fixed
  this — gated real-model tests exist (`LMR_DIARIZATION_TESTS=1`,
  `LMR_OLLAMA_TESTS=1`, `LMR_OPENAI_SUMMARY_TESTS=1`, `LMR_OPENROUTER_SUMMARY_TESTS=1`)
  but have never actually been run.

Original Phase 5 roadmap (AI summaries) [IMPLEMENTED, see Phase 5E]:
- `summarization/ollama_client.py` — done, plus OpenAI/OpenRouter
- Structured summary schema (topics, decisions, action items, questions) — done
- Chunking + map-reduce aggregation for long meetings — **not done**; a transcript is
  sent to the provider in one request regardless of length. Fine for a typical meeting
  within a model's context window; an open gap for very long transcripts.
- Prompt-injection hardening: transcript delimited, no tool use — done (session 10 fix;
  this had been skipped when the feature was first built)
- Retry if model unavailable — manual retry only (the user clicks Regenerate); no
  automatic retry/backoff on a transient failure

Phase 5F — Summary export and evidence [IMPLEMENTED, see IMPLEMENTATION_STATUS.md —
session 10]:
- `summary.md` Markdown export of the latest summary version (title, date,
  provider/model, executive summary, topics, decisions, action items, speakers,
  resolved timestamp references)
- `Decision.segment_refs`/`ActionItem.segment_refs`: additive, backward-compatible
  indices into the transcript's segments, when the model could identify supporting
  evidence — never invented, never required
- Storage architecture intentionally unchanged for this pass (flat
  `recordings/<session_id>/` per meeting, no database, no year/month hierarchy, no
  folder migration) — see SECURITY_AND_PRIVACY.md/ARCHITECTURE.md for the storage
  layout as it actually exists today

### Phase 6 — Packaging [IMPLEMENTED, see IMPLEMENTATION_STATUS.md]
- PyInstaller spec with helper in `Contents/Helpers/`
- `Info.plist`: `LSUIElement`, `NSMicrophoneUsageDescription`, `NSScreenCaptureUsageDescription`
- Ad-hoc signed (helper + bundle); Developer ID signing + notarization documented but
  not performed — needs an Apple Developer Program enrollment this agent cannot obtain
- Launch test on bare machine (outside PyCharm) — done via a fresh terminal launch

### Phase 6B — CPU-First, Low-Resource and Cross-Platform Readiness [IMPLEMENTED, see
IMPLEMENTATION_STATUS.md]
- Explicit CPU device for both local AI models (Whisper via faster-whisper,
  diarization via pyannote) — never auto-detected, never a hidden GPU dependency.
  "base" is now the recommended default Whisper model (was "small")
- `settings/profiles.py`: a single "Low Resource / Balanced / Quality" processing
  profile that sets the recommended Whisper model, caps CPU threads, and
  enables/disables diarization — instead of exposing every individual knob
- `infrastructure/resource_governor.py`: an app-wide (not per-session) limiter so two
  different recordings' transcription/diarization/summarization jobs never run
  concurrently on "Low Resource" — unlimited (unchanged) on Balanced/Quality
- PyInstaller spec excludes the ~390MB `pyannote.audio`/`torch` dependency tree from
  the default packaged app — diarization needs a developer-built "full" variant (not
  yet built) or running from source with the extra installed
- `scripts/install_diarization_cpu.{sh,ps1}`: install the CPU-only PyTorch wheel
  (PyPI's default on Windows/Linux is the CUDA build) instead of the multi-gigabyte
  CUDA runtime nobody asked for
- Windows and Linux blockers inventoried (Phase 7 below expanded from 3 bullets into
  a real gap analysis) — no Windows backend code was written, per explicit scope

### Phase 7 — Windows [FUTURE — blockers inventoried in Phase 6B, nothing implemented]

A gap analysis against the real `capture/protocols.py::CaptureBackend` Protocol (not a
generic "needs Windows support" note) — every method a Windows backend must implement:
`check_permissions`, `request_permissions`, `list_displays`, `start`, `stop`,
`shutdown`, plus `is_recording`.

**Real blockers (not yet resolved):**
1. **No Windows capture code exists at all** — not even a stub. `capture/factory.py`
   raises `HelperUnavailableError` for any non-`darwin` platform today; `sys.platform
   == "win32"` has no branch.
2. **No Windows development or test environment is available to this project** — every
   Windows claim below is design analysis from documented platform APIs, not something
   this agent (macOS-only sandbox) has built or run. Any Windows implementation can
   only be verified by a human with real Windows hardware, the same way
   `tests/capture/` is gated by `LMR_HW_TESTS=1` and a real Mac today.
3. **Screen + audio capture technology choice is still open, with a real architectural
   fork**: (a) mirror the macOS design — a small native helper process (C++ or C#/.NET;
   .NET gives faster access to WinRT `Windows.Graphics.Capture` projections and WASAPI
   via NAudio, at the cost of a larger self-contained deployment, roughly 60–150MB) that
   the Python app talks to, matching the existing `HelperClient`/JSON-lines pattern; or
   (b) skip a native helper entirely and capture directly from Python via
   `pycaw`(WASAPI)/`dxcam`/`mss`(screen) — meaningfully less work, but less control over
   encoding and likely needs ffmpeg (or another Python encoder) in the loop for the
   output container. Neither choice is made yet; this needs a decision before any code
   is written, not an assumption that the Swift helper's shape is the only option.
4. **Likely APIs, if the native-helper path is chosen** (standard, well-documented
   Windows mechanisms, not verified against a live implementation): screen —
   `Windows.Graphics.Capture` (Windows 10 version 1803+, broader capability from
   2004+) or the lower-level DXGI Desktop Duplication API; system-audio loopback and
   microphone — WASAPI (`IAudioClient` with `AUDCLNT_STREAMFLAGS_LOOPBACK` for "what
   you hear", standard capture mode for the microphone); encoding — Media Foundation
   Sink Writer, or hand the raw frames to ffmpeg (already a runtime dependency on every
   platform) instead of a native encoder.
5. **The two-audio-track guarantee must be explicitly preserved, not assumed.** Phase
   5A/B/C (source separation, diarization) all depend on the recording file containing
   system and microphone audio as genuinely separate tracks — verified on macOS against
   real hardware (`native/macos/PROTOCOL.md`'s `started.tracks`). Whatever Windows
   encoding path is chosen must be verified the same way, with `ffprobe` against a real
   Windows recording — never assumed from documentation alone (same standard this
   project already holds itself to for macOS, see CLAUDE.md's ground rules).
6. **Permission/consent model does not map 1:1.** macOS's TCC (Screen Recording /
   Microphone grants, surfaced via `PermissionStatus`/`request_permissions()`) has no
   exact Windows equivalent for a standard (unpackaged) desktop app — WASAPI loopback
   and Desktop Duplication/Graphics.Capture generally don't require an OS-level consent
   grant the way macOS does, though Windows 11 shows its own capture indicator/
   notification outside the app's control. A Windows `CaptureBackend.check_permissions()`
   would likely just report "granted" unconditionally for both — which the existing
   `consent_dialog.py` (the app's own, OS-independent "has everyone been informed?"
   popup) already covers regardless. Needs a product decision, not a blocker to
   capture itself.
7. **ffmpeg/ffprobe are not bundled or guaranteed present on Windows** — the Python
   side assumes they're on PATH (true in most macOS dev setups via Homebrew, not true
   out of the box on Windows). Packaging/bundling ffmpeg for Windows (or a first-run
   "please install ffmpeg" flow) is a new, Windows-specific requirement, not something
   Phase 6's packaging work already covers.
8. **`infrastructure/single_instance.py` is POSIX-only** (already documented in its own
   module docstring) — currently a silent no-op on Windows rather than a hard failure;
   needs a named-mutex-based implementation.
9. **PyInstaller packaging for Windows** — an NSIS or MSI installer, plus the same
   "exclude the diarization dependency tree by default" discipline Phase 6B's macOS
   spec now has; not yet attempted.

**Smaller than it looks:** cloud credential storage (OpenAI/OpenRouter/Hugging Face
tokens) is *not* a real blocker — `infrastructure/credentials/factory.py` already
returns `UnavailableCredentialStore` on non-macOS today, but the `keyring` package
(already a core dependency) ships a Windows Credential Manager backend
(`keyring/backends/Windows.py`, confirmed present in this project's own installed
`keyring`) with the identical `get_password`/`set_password`/`delete_password` calls
`MacOSKeychainStore` already uses. A `WindowsCredentialManagerStore` (or a single
platform-neutral `KeyringCredentialStore` replacing both) is close to a rename, not new
design — deliberately not implemented in this pass since it is credential storage, not
"the Windows backend," and doing it now would have been scope creep beyond the explicit
instruction to only inventory Windows blockers.

**Suggested phased order, once someone decides to actually build this:**
1. Decide the architecture fork (native helper vs. pure-Python capture) — a real product
   decision, not an engineering detail.
2. Build the chosen capture path as a throwaway script first (same spirit as the macOS
   helper's own development) and verify with `ffprobe` that it produces a video track
   plus two independent audio tracks, on real Windows hardware.
3. Implement `capture/windows/backend.py` against the existing `CaptureBackend`
   protocol — nothing above `capture/factory.py` should need to change, by design.
4. `infrastructure/single_instance.py`'s named-mutex branch.
5. `WindowsCredentialManagerStore` (see above — the easy win).
6. ffmpeg availability story (bundle vs. first-run install flow) and the NSIS/MSI
   installer.
7. A hardware-gated test suite mirroring `tests/capture/` (`LMR_HW_TESTS=1`), run by a
   human with real Windows hardware — this project's own standard for "don't claim
   capture works because it compiles" applies identically here.

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
