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
│  SessionDirectory  ·  session.json                      │
│  SQLite + FTS5 (Phase 4)                               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Transcription Layer (Phase 4)             │
│  TranscriptionBackend protocol                          │
│  FasterWhisperBackend  ·  TranscriptionJob              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Summarization Layer (Phase 5)             │
│  SummarizationBackend protocol                          │
│  OllamaClient  ·  ChunkAggregator                      │
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
- `tray.py` — `TrayController`: `QSystemTrayIcon`, dynamic menu items, recording-state styling
- `consent_dialog.py` — Pre-recording consent: explains what will be captured, where it's saved, and requires explicit confirmation
- `permission_guide.py` — Actionable instructions when a permission is denied (deep-links to System Settings)
- `settings_dialog.py` — Configuration UI (display selection, audio devices, output path, model selection)
- `library_window.py` — Meeting list with search, playback, transcript view (Phase 4)

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
- `database.py` — SQLite schema, migrations, FTS5 (Phase 4)

`session.json` schema:
```json
{
  "session_id": "lmr_20251015T143022_a3f9",
  "status": "in_progress",
  "started_at": "2025-10-15T14:30:22.000Z",
  "finalized_at": null,
  "display_id": 1,
  "recording_path": "recording.mov",
  "mixdown_path": null,
  "transcript_path": null,
  "summary_path": null,
  "error": null
}
```

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
21. (Phase 4) TranscriptionJob queued; runs after finalization completes
```

---

## Windows Support Path

The `CaptureBackend` protocol is platform-independent. Adding Windows support requires only:

1. `capture/windows/backend.py` — implement `CaptureBackend` using Windows.Graphics.Capture + WASAPI loopback
2. A Windows-native helper (C# or Rust) following the same JSON-lines protocol
3. `scripts/build_helper_windows.bat` + PyInstaller Windows spec

No changes required in the application, media, storage, transcription, or summarization layers.

---

## Security and Privacy Design

See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md). Key points:
- Recordings never leave the device (default)
- Consent required before every recording start
- Logs never contain audio content, transcript text, or credentials
- Swift helper has minimal surface area; no network access
- No auto-start, no hidden recording, no notification evasion
