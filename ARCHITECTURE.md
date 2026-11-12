# ARCHITECTURE.md — LocalMeetingRecorder

## Overview

LocalMeetingRecorder is a macOS menu-bar application with a layered, platform-abstracted architecture. Python owns the application lifecycle, UI, storage, transcription, and summarization. A minimal Swift helper exclusively handles real-time capture (ScreenCaptureKit + AVAssetWriter), communicating with Python over a documented JSON-lines protocol.

```
┌─────────────────────────────────────────────────────────┐
│                  Presentation Layer                      │
│  PySide6 QSystemTrayIcon  ·  Consent Dialog             │
│  Permission Guide  ·  Settings  ·  Recording Library    │
└──────────────────────────┬──────────────────────────────┘
                           │ signals / slots
┌──────────────────────────▼──────────────────────────────┐
│                  Application Layer                       │
│  RecordingCoordinator (state machine)                   │
│  SessionManager  ·  IncompletSessionRecovery            │
└────────────┬────────────────────────────┬───────────────┘
             │                            │
┌────────────▼──────────┐    ┌────────────▼───────────────┐
│    Capture Layer       │    │    Media Layer              │
│  CaptureBackend proto  │    │  MediaInspector (ffprobe)  │
│  MacOSCaptureBackend   │    │  PostProcessor (ffmpeg)    │
│  HelperClient          │    │  SessionFinisher            │
│  WindowsUnsupported    │    └────────────────────────────┘
└────────────┬──────────┘
             │ JSON lines stdin/stdout
┌────────────▼──────────────────────────────────────────┐
│              native/macos/lmr-capture-helper          │
│  (Swift 6.2, ScreenCaptureKit, AVAssetWriter)         │
│  · SCStreamConfiguration: video + system + mic audio  │
│  · Single AVAssetWriter, 3 inputs, shared SCK clock   │
│  · Exits on stdin EOF (parent crash safety)           │
└────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                  Persistence Layer                      │
│  StoragePaths (Application Support)                     │
│  Flat recordings/<session_id>/ per meeting:              │
│  session.json · transcript.json/.txt · summary.json/.md │
│  No database — plain files, atomic temp-file-then-rename│
│  writes everywhere. (An earlier SQLite+FTS5 sketch for   │
│  cross-session search was never built — deliberately     │
│  out of scope; see IMPLEMENTATION_STATUS.md Phase 5F.)   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Transcription Layer (Phase 4/5)           │
│  AudioExtractor (media/) · TranscriptionEngine          │
│  TranscriptStore · TranscriptionManager (background job)│
│  TranscriptionProviderClient (Phase 4.5): local or cloud│
│  OpenAITranscriptionClient · OpenRouterTranscriptionClient│
│  Source-separated (Phase 5A/B) + diarized (Phase 5C)     │
│  segments merged onto one Transcript                     │
│  CPU-only, explicit: device="cpu"/compute_type="int8"    │
│  always, never auto-detected (Phase 6B)                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Speaker Diarization Layer (Phase 5C)       │
│  SpeakerDiarizationEngine protocol                       │
│  PyannoteSpeakerDiarizationEngine (pyannote.audio,       │
│  lazy-loaded, Hugging-Face-gated model)                  │
│  speaker/assignment.py: overlap-based segment -> speaker │
│  CPU-only, explicit: pipeline.to(torch.device("cpu"))    │
│  always called, never relies on pyannote's own default   │
│  (Phase 6B); excluded from the default PyInstaller build │
│  (~390MB dependency tree — see packaging/macos/*.spec)   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Summarization Layer (Phase 5E/5F)          │
│  SummaryProviderClient protocol · SummaryManager          │
│  OllamaSummaryClient · OpenAISummaryClient ·             │
│  OpenRouterSummaryClient (independent of the             │
│  transcription provider/model)                           │
│  summary.json (versioned) + summary.md (latest, with     │
│  segment_refs resolved to timestamps)                    │
│  No chunking/map-reduce yet — one request per transcript │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│       Resource Governance Layer (Phase 6B)              │
│  ProcessingProfile (Low Resource/Balanced/Quality) ·     │
│  settings/profiles.py (pure profile -> behavior lookup)  │
│  ResourceGovernor: app-wide (not per-session) limiter —  │
│  caps concurrent transcription/diarization/summarization │
│  jobs across *different* sessions; recording itself never│
│  goes through this, so it is never blocked by AI load    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│        Settings + Credentials Layer (Phase 4.5)         │
│  SettingsService (settings.json)  ·  AppSettings        │
│  CredentialStore protocol  ·  MacOSKeychainStore        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                Infrastructure Layer                     │
│  Settings (pydantic-settings)  ·  StructuredLogger      │
│  SubprocessManager  ·  Diagnostics  ·  Packaging        │
└─────────────────────────────────────────────────────────┘
```

---

## Layer Responsibilities

### 1. Presentation Layer (`src/local_meeting_recorder/ui/`)

**Does:** Render the tray icon, menus, dialogs, and progress indicators. Forward user actions to the application layer via Qt signals.

**Does NOT:** Make recording decisions, access the file system directly, or interact with capture APIs.

Key files:
- `tray.py` — `TrayController`: `QSystemTrayIcon`, dynamic menu items, recording-state styling, a (Phase 4) transcription-status suffix per recent recording, a "Settings…" item (Phase 4.5), and a clickable tray notification that opens Settings when a transcription fails for a missing API key
- `consent_dialog.py` — Pre-recording consent: explains what will be captured, where it's saved, and requires explicit confirmation
- `permission_guide.py` — Actionable instructions when a permission is denied (deep-links to System Settings)
- `transcript_dialog.py` — (Phase 4) Read-only transcript viewer with detected language and a Retry action when the last attempt failed; opened from a recent-recording click instead of Finder once a transcript exists
- `transcription_bridge.py` — (Phase 4) Re-emits `TranscriptionManager` callbacks as Qt signals, same pattern as `coordinator_bridge.py`
- `settings_dialog.py` — (Phase 4.5) General/Transcription/About — see "Settings + Credentials Layer" below
- `library_window.py` — Meeting list with search, playback — not yet built; the transcript dialog covers viewing for now

### 2. Application Layer (`src/local_meeting_recorder/application/`)

**Does:** Manage recording state, coordinate between capture and storage, handle recovery.

**Does NOT:** Know about macOS APIs, UI widgets, or file formats.

Key files:
- `recording_coordinator.py` — `RecordingCoordinator`: enforces state transitions, owns session lifecycle
- `session_manager.py` — Generates session IDs, creates/finalizes session directories and `session.json`
- `recovery.py` — Detects and offers recovery of incomplete sessions on launch

State machine:
```
Idle
  │─[start_recording()]──► Preparing
  │                           │─[helper ready]──► Recording
  │                           │─[permission denied]──► Error
  │                           │─[stop_recording()]──► Finalizing─► Idle
  │                        Recording
  │                           │─[stop_recording()]──► Finalizing
  │                           │─[helper crash]──► Error
  │                        Finalizing
  │                           │─[complete]──► Saved──► Idle
  │                           │─[error]──► Error
  └─[Error: reset()]──► Idle
```

### 3. Capture Layer (`src/local_meeting_recorder/capture/`)

**Does:** Provide a platform-independent interface to recording hardware. Launch, communicate with, and supervise the native helper.

**Does NOT:** Encode video, write files, or know about UI state.

Key files:
- `protocols.py` — `CaptureBackend`, `PermissionStatus`, `DisplayInfo`, `CaptureEvent` protocols
- `coordinator.py` — Capture-layer coordinator; maps helper events to domain events
- `fake.py` — `FakeCaptureBackend` for unit/integration tests (never shipped as a feature)
- `macos/backend.py` — `MacOSCaptureBackend`: platform check, permissions, display list, delegates to `HelperClient`
- `macos/helper_client.py` — `HelperClient`: subprocess lifecycle, JSON-lines reader thread, watchdog timer
- `macos/permissions.py` — `PermissionChecker`: checks and requests TCC grants using pyobjc or `tccutil`
- `windows/backend.py` — `UnsupportedPlatformBackend`: raises `NotImplementedError` with clear message

### 4. Media Layer (`src/local_meeting_recorder/media/`)

**Does:** Inspect and post-process recorded files. Does not perform real-time encoding.

Key files:
- `inspector.py` — `MediaInspector`: wraps `ffprobe`, reports streams, duration, RMS levels
- `postprocess.py` — `PostProcessor`: mixdown to MP4, WAV extraction for transcription, partial-file recovery
- `timestamps.py` — Session timing helpers, onset analysis

### 5. Persistence Layer (`src/local_meeting_recorder/storage/`)

Key files:
- `paths.py` — `StoragePaths`: all `~/Library/Application Support/LocalMeetingRecorder/` paths
- `session_store.py` — `session.json` read/write, recovery state management
- `database.py` — SQLite schema, migrations, FTS5 full-text search across transcripts — deferred; Phase 4 shipped file-based transcript storage (`transcript.json`/`transcript.txt` per session, read directly for the transcript viewer) instead, since nothing yet needs cross-session search

`session.json` schema (abridged — see `domain/session_metadata.py` for the exact, current fields):
```json
{
  "session_id": "lmr_20251015T143022_a3f9",
  "status": "saved",
  "started_at": "2025-10-15T14:30:22.000Z",
  "finalized_at": "2025-10-15T14:34:02.000Z",
  "display_id": 1,
  "recording_file": "recording.mov",
  "post_processing": {
    "transcript_file": "transcript.json",
    "transcript_status": "done",
    "transcript_language": "en",
    "transcript_model": "small",
    "transcript_error": null,
    "summary_file": null
  },
  "error_code": null,
  "error_message": null
}
```
`post_processing.transcript_status` is one of `not_started | running | done | failed`
(`domain/session_metadata.py::TranscriptionStatus`) and is updated independently of the
session's own `status` — a failed transcription never flips a `saved` recording to
anything else.

### 6. Swift Native Helper (`native/macos/`)

**Purpose:** The only reliable path to capture system audio on macOS without a kernel extension. ScreenCaptureKit grants access to system audio through `SCStreamConfiguration.capturesAudio` (macOS 13+) and microphone through `SCStreamConfiguration.captureMicrophone` (macOS 15+).

**Protocol — JSON lines on stdin/stdout:** see [native/macos/PROTOCOL.md](native/macos/PROTOCOL.md)
for the full, verified command/event reference (field names, defaults, error codes,
lifecycle). Modeled in Python by `capture/protocols.py`. Two corrections worth calling out
since they are easy to get wrong: the microphone field is `capture_microphone`, not
`capture_mic`, and nothing in the protocol ever carries a `session_id` — the helper only
ever knows an output path.

**Crash safety:** Helper monitors stdin; when Python exits or crashes, stdin becomes EOF and the helper calls `finishWriting()` and exits within 5 seconds.

**Info.plist embedded:** `NSMicrophoneUsageDescription`, `NSScreenCaptureUsageDescription`, bundle ID (`com.localmeetingrecorder.capture-helper`) are embedded at link time so TCC attributes permission requests to the bundle identifier rather than `swiftc`'s anonymous binary.

### 7. Infrastructure Layer (`src/local_meeting_recorder/infrastructure/`)

Key files:
- `logging_setup.py` — Structured JSON logging; session-ID context; never logs audio content or transcript
- `subprocess_manager.py` — Bounded subprocess lifecycle: start, watchdog, graceful stop (SIGTERM → SIGKILL after 5 s), await
- `settings.py` — Forwarded from `config.py`; `LMR_` env prefix, TOML file at `~/Library/Application Support/LocalMeetingRecorder/config.toml`
- `diagnostics.py` — System info report for bug reports (OS, audio devices, helper version, permissions)

### 8. Transcription Layer (`src/local_meeting_recorder/transcription/`, `media/audio_extractor.py`)

**Does:** Turn a finished recording into a transcript, as a background job that never
blocks the UI and never touches the original recording file.

**Does NOT:** Know about Qt, the coordinator, or the capture backend — `TranscriptionManager`
only needs a session's metadata and the path `SessionStore` already resolves.

Key files:
- `media/audio_extractor.py` — `AudioExtractor`: `extract_wav16k_mono()` mixes down
  every audio track (Phase 4's single combined transcript); `extract_track_wav16k_mono()`
  (Phase 5) isolates exactly one track — no mixing — by its ffprobe stream index; both to
  mono 16 kHz PCM WAV (the format faster-whisper expects); raises a typed error for a
  missing audio stream, an invalid source file, or an ffmpeg failure
- `transcription/engine.py` — `TranscriptionEngine`: wraps `faster_whisper.WhisperModel`,
  loaded lazily on first `transcribe()` call (never at construction, never at app
  startup); supports explicit language selection or automatic detection, streams
  progress via an `on_segment` callback
- `transcription/models.py` — `Transcript`/`TranscriptSegment` (Pydantic): ordered,
  timestamped segments, detected language, model name; validates segment ordering.
  `TranscriptSegment.source` (Phase 5, `AudioSource.SYSTEM`/`MICROPHONE`/`None`) tags
  which capture track a segment came from — `None` for a Phase 4 combined transcript, so
  old `transcript.json` files load unchanged. `Transcript.source_errors` records which
  source(s) failed for a partial result
- `transcription/store.py` — `TranscriptStore`: atomic (`temp file + rename`) write of
  `transcript.json`/`transcript.txt` into the session directory, same pattern as
  `storage/session_store.py`
- `transcription/manager.py` — `TranscriptionManager`: one background thread per
  session, duplicate-run prevention, persists `post_processing.transcript_status`
  (`not_started → running → done`/`failed`/`partial`) via `SessionStore` — never the
  session's own `status`, which a failed transcription never touches. When a session's
  `audio_tracks` has both a system and a microphone entry (true for essentially every
  real recording — see "Verified Capture" and IMPLEMENTATION_STATUS.md's Phase 5 "Part
  A"), extracts and transcribes each independently (one engine instance, reused for
  both) and merges the segments onto one chronological, source-tagged timeline; one
  source failing finishes the job `partial` rather than `failed`, as long as the other
  succeeded. Falls back to the original single-pass mixdown when only one track exists
  (microphone capture disabled, or a legacy session with no track metadata)
- Wired from `ui/app_controller.py`: `RecordingState.SAVED` triggers
  `TranscriptionManager.transcribe_async()`; a recent recording's click opens
  `ui/transcript_dialog.py` (with a Retry action) once a transcript exists, is partial,
  or failed, falling back to the existing "open in Finder" behavior otherwise
- `transcription/providers/` (Phase 4.5) — cloud alternatives to the local engine,
  behind the same shape (`protocol.py`'s `TranscriptionProviderClient`):
  `http_base.py` (shared multipart/verbose_json request + cheap key-test logic,
  stdlib `urllib` only), `openai_client.py`, `openrouter_client.py`, `factory.py`
  (`build_provider_client()`: picks local vs. cloud from `AppSettings` +
  `CredentialStore`, raises a distinguishable `TranscriptionError(code="missing_api_key")`
  for a cloud provider with no key — never a fatal error, never touching the recording)

### 9. Settings + Credentials Layer (`src/local_meeting_recorder/settings/`,
`infrastructure/credentials/`) (Phase 4.5)

**Does:** Own the Settings-window-managed user preferences (recording storage location,
transcription provider/model) and secret storage (API keys), both independent of Qt.

**Does NOT:** Decide which recording state transitions are legal, touch a recording file,
or know about the Swift helper.

Key files:
- `settings/models.py` — `AppSettings` (Pydantic, versioned via `schema_version`),
  `TranscriptionProviderId`, `ProviderAvailability`
- `settings/service.py` — `SettingsService`: atomic (`temp file + rename`) read/write of
  `settings.json` in the Application Support root, same pattern as
  `storage/session_store.py`; falls back to defaults on a missing/corrupt/invalid file
  rather than crashing. `apply_recordings_dir_override()` applies a persisted directory
  choice to `StoragePaths` at startup, catching an unusable directory (e.g. an unmounted
  external drive) and reporting it rather than crashing
- `infrastructure/credentials/protocol.py` — `CredentialStore`: `get`/`set`/`delete`/
  `is_available`, implemented by:
  - `macos_keychain.py` — `MacOSKeychainStore`, via the `keyring` package (ctypes
    bindings to Security.framework). Chosen over shelling out to the `security` CLI
    because `security add-generic-password -w <secret>` puts the secret on the
    subprocess's argv, briefly visible to `ps` — a real security tradeoff, not a style
    preference (decision D14 in `.claude/project-memory.md`)
  - `unavailable.py` — `UnavailableCredentialStore`: the explicit non-macOS placeholder;
    raises a clear error on every call rather than silently falling back to plaintext.
    A future Windows backend (Phase 7) replaces this via `factory.py`'s platform
    dispatch without any caller changing
  - `memory.py` — `InMemoryCredentialStore`: test-only, never wired as a production
    fallback
  - `masking.py` — `mask_secret()`: all but the last 4 characters, used everywhere a
    saved key is displayed
- `ui/settings_dialog.py` — `SettingsDialog`/`SettingsDialogContext`: General (storage
  location + native folder picker), Transcription (provider dropdown showing live
  `ProviderAvailability`, local Whisper model size, masked API key with Save/Remove,
  Test Connection), About. Every change applies immediately (no Save button, matching
  macOS's own System Settings panes) except the API key, which needs an explicit Save
  so a half-typed key is never persisted

---

## Data Flow — Recording Session

```
1. User clicks "Start Recording"
2. TrayController emits start_requested signal
3. RecordingCoordinator: Idle → Preparing
4. ConsentDialog shown: user confirms, OR cancels → back to Idle
5. PermissionChecker checks Screen Recording + Microphone TCC status
   - If denied: show PermissionGuide → coordinator → Error
6. SessionManager creates session dir + writes session.json {status: in_progress}
7. HelperClient.start(output_path, display_id, capture_mic=True)
   - Spawns lmr-capture-helper process
   - Sends {"cmd": "start", ...}
   - Reader thread parses JSON events
8. Helper responds {"event": "started"} → coordinator: Preparing → Recording
9. TrayController shows recording indicator + timer
10. Helper streams {"event": "stats"} every 5 s
11. User clicks "Stop Recording"
12. RecordingCoordinator: Recording → Finalizing
13. HelperClient sends {"cmd": "stop"}, awaits {"event": "stopped"}
14. Helper calls finishWriting() on AVAssetWriter, emits stopped event
15. HelperClient process exits, watchdog cancelled
16. MediaInspector validates recording (stream count, duration, RMS)
17. PostProcessor creates mixdown MP4 + 16 kHz WAV (for transcription)
18. SessionManager updates session.json {status: saved, finalized_at: ...}
19. coordinator: Finalizing → Saved → Idle
20. TrayController reverts to idle state
21. AppController sees state == Saved, calls TranscriptionManager.transcribe_async()
    (Phase 4) — a new background thread, one per session, with duplicate-run prevention
22. AudioExtractor mixes every audio track down to 16 kHz mono WAV (ffmpeg) in a temp dir
23. TranscriptionEngine lazily loads a faster-whisper model, transcribes, emits progress
24. TranscriptStore writes transcript.json + transcript.txt atomically into the session dir
25. session.json's post_processing.transcript_status moves not_started → running → done
    (or failed — this never touches the recording's own status, which stays saved)
26. Tray shows status in Recent Recordings; clicking a transcribed/failed entry opens a
    transcript viewer/retry dialog instead of just the Finder folder
27. If diarization is enabled (Phase 5C) and a diarization job actually starts,
    AppController waits for it to finish (success or failure) before starting
    summarization — see step 29; TranscriptionManager.diarize_async() re-extracts only
    the system track, runs PyannoteSpeakerDiarizationEngine, and updates only the
    system segments' speaker_id on the existing transcript (never re-transcribes, never
    touches microphone segments)
28. If diarization is disabled, or no diarization job actually started, summarization
    starts immediately after transcription instead of waiting
29. AppController calls SummaryManager.generate_async() — another background thread,
    one per session; it reads transcript.text_with_segment_indices (not the plain
    text), sends it to whichever summary provider is configured (Ollama/OpenAI/
    OpenRouter, independent of the transcription provider), and validates the
    response against a shared structured JSON schema
30. SummaryStore appends the new MeetingSummary as another version in summary.json
    (never overwriting earlier versions) and summarization/markdown.py renders
    summary.md from the latest version, resolving any segment_refs to timestamps
    against the transcript — a markdown-rendering failure is logged and does not
    turn an otherwise-successful summary into a reported failure
```

---

## CPU-First and Resource Governance Design (Phase 6B)

A hard product requirement: this app must run on an ordinary computer with no NVIDIA
GPU, no CUDA, integrated graphics only, and limited RAM — CPU-only is the default, and
recording must stay reliable even when AI processing is slow.

- **Explicit, never auto-detected devices.** `TranscriptionEngine` (faster-whisper) and
  `PyannoteSpeakerDiarizationEngine` (pyannote.audio) both pin `device="cpu"` in code —
  the former because the underlying `faster_whisper.WhisperModel` itself defaults to
  `device="auto"` (which can select CUDA) if not told otherwise; the latter because
  `pyannote.audio`'s pipeline would otherwise rely on whatever device a loaded
  checkpoint's config or `torch.load`'s own default happens to pick. Nothing in this
  application ever calls `torch.cuda.is_available()` to make a decision.
- **One profile, not a dozen knobs.** `settings/profiles.py`'s `ProcessingProfile`
  (Low Resource / Balanced / Quality) is a pure lookup table from one user choice to:
  the recommended Whisper model size, a CPU-thread cap passed to both
  faster-whisper/ctranslate2 and torch, whether diarization is allowed at all, and the
  `ResourceGovernor` concurrency limit below.
- **`infrastructure/resource_governor.py`** caps how many heavy AI jobs — across
  *different* sessions, not just within one — run at once, app-wide. Transcription's
  own per-session duplicate-prevention (`TranscriptionManager._running`) only ever
  stopped a session from racing itself; two different recordings saved back to back
  could previously both load a model and run inference concurrently regardless of
  profile. On Low Resource, the governor's limit is 1 — a Whisper job and a diarization
  job (even for different sessions) can never run at the same time. On Balanced/Quality
  it is unlimited, identical to pre-Phase-6B behavior. Recording itself never acquires
  anything from the governor, so it is never blocked by AI load, by construction.
- **Packaging stays lean by default.** The diarization dependency tree
  (`torch`/`pyannote.audio` and everything they pull in, ~390MB installed) is excluded
  from the default PyInstaller spec (`packaging/macos/LocalMeetingRecorder.spec`) even
  when it happens to be installed in the build environment — see the Speaker
  Diarization layer box above.

## Windows Support Path

The `CaptureBackend` protocol (`capture/protocols.py`) is platform-independent, and
nothing above `capture/factory.py` (application, media, storage, transcription,
summarization) needs to change *in principle* to add a `capture/windows/backend.py`
implementing it — `capture/factory.py` already documents exactly this.

That said, no Windows capture code exists yet (not even a stub), and getting there is
more than three bullet points — see **PLAN.md's Phase 7 section** for the real gap
analysis: the screen/audio capture technology choice is a genuine open fork (native
helper vs. pure-Python capture, not an assumed Swift-helper-equivalent), the
permission/consent model doesn't map 1:1 from macOS's TCC, ffmpeg isn't guaranteed
present on a Windows machine the way it often is in a macOS dev setup, and no Windows
development or test environment has been available to verify any of this. Cloud
credential storage is the one piece that's genuinely close to free: `keyring` (already
a core dependency) ships a Windows Credential Manager backend with the same API
`MacOSKeychainStore` already uses.

---

## Security and Privacy Design

See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md). Key points:
- Recordings never leave the device (default)
- Consent required before every recording start
- Logs never contain audio content, transcript text, or credentials
- Swift helper has minimal surface area; no network access
- No auto-start, no hidden recording, no notification evasion
