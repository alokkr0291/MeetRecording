# IMPLEMENTATION_STATUS.md

Source of truth for progress across sessions. Update at the end of every working session.

Last updated: 2026-11-12 (session 14)

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

## Phase 4 — Local Speech-to-Text Transcription (session 7)

| Task | Status | Notes |
|---|---|---|
| `media/audio_extractor.py` (`AudioExtractor`) | DONE | ffmpeg mixdown of every audio track (`amix` when >1 stream) to 16 kHz mono PCM WAV; validates output via the stdlib `wave` module; typed errors: `NoAudioStreamError`, `AudioExtractionError`, `MediaError` |
| `transcription/engine.py` (`TranscriptionEngine`) | DONE | Wraps `faster_whisper.WhisperModel`, loaded lazily on first `transcribe()` — never at construction or app startup; explicit `language=` or `None` for auto-detect; `on_segment` progress callback; `TranscriptionError`/`TranscriptionEmptyAudioError` |
| `transcription/models.py` (`Transcript`, `TranscriptSegment`) | DONE | Pydantic, validates segment ordering (`end_s >= start_s`, non-decreasing `start_s`) |
| `transcription/store.py` (`TranscriptStore`) | DONE | Atomic (temp file + `os.replace`) write of `transcript.json`/`transcript.txt`, same pattern as `SessionStore.write` |
| `transcription/manager.py` (`TranscriptionManager`) | DONE | One background thread per session; duplicate-run prevention (`_running` set); retry by calling `transcribe_async` again; persists `post_processing.transcript_status` (`not_started→running→done`/`failed`) — **never** the session's own `status`, which stays `saved` regardless; every UI callback invocation is guarded (`_safe_callback`) so a torn-down Qt sink can never crash the job or be mistaken for a transcription failure (found via a real thread-leak warning in `tests/unit/test_app.py`, not hypothetical — see below) |
| `domain/session_metadata.py` additions | DONE | `TranscriptionStatus` enum; `PostProcessingArtifacts.transcript_status/_language/_model/_error`; `mark_transcription_running/done/failed()` — all additive, old `session.json` files still load with defaults |
| `config.py` additions | DONE | `whisper_model` (existing placeholder, now used), `whisper_device`, `whisper_compute_type` |
| UI: `ui/transcription_bridge.py` | DONE | Re-emits `TranscriptionManager` callbacks as Qt signals, one-way (manager → UI), same pattern as `coordinator_bridge.py` |
| UI: `ui/transcript_dialog.py` | DONE | Read-only viewer (detected language + segment text) with a Retry button when the last attempt failed |
| UI: `ui/tray.py` | DONE | Recent-recordings label gets a status suffix ("Transcribing… NN%", "Transcribed (en)", "Transcription failed") — additive; default (`not_started`) status shows no suffix, so all prior tray tests pass unchanged |
| UI: `ui/app_controller.py` | DONE | `RecordingState.SAVED` triggers `transcribe_async()`; a recent-session click opens the transcript dialog when a transcript exists or failed, otherwise falls back to the existing "open in Finder" behavior |
| `app.py` wiring | DONE | Composition root builds `TranscriptionManager` + `TranscriptionBridge` unconditionally alongside the existing coordinator/bridge |
| Unit tests (mocked model, no download) | DONE | `tests/unit/transcription/{test_models,test_engine,test_store,test_manager}.py` — 32 new tests; `test_engine.py` monkeypatches `WhisperModel` with a scripted fake (lazy-load, explicit/auto language, segment order, empty audio, model/inference failure) |
| Integration tests (real ffmpeg) | DONE | `tests/integration/media/test_audio_extractor.py` — multi-track mixdown, single-track, missing audio stream, invalid media, missing source file |
| UI tests | DONE | Extended `tests/unit/ui/test_tray.py` and `tests/unit/ui/test_app_controller.py` — trigger-on-SAVED, duplicate-prevention via a fake manager, dialog vs. Finder-folder routing, retry-button wiring |
| Real-model integration test | DONE | `tests/transcription/test_real_whisper.py`, gated by `LMR_WHISPER_TESTS=1` (not part of the default suite — downloads a model). **Actually run** this session: real speech synthesized with macOS `say`, converted to 16 kHz mono WAV, transcribed by the real "tiny" model; output contained recognizable words from the spoken sentence. |
| Full suite green | DONE | `bash scripts/test.sh`: 216 tests (173 existing + 43 new), ruff + mypy clean; repeated 4x with no flakiness |

### Dependency issue found and fixed this session

`faster-whisper` 1.2.1 declares only `av>=11` (no upper bound). PyPI's latest `av`
(19.0.2) removed the `metadata_errors` keyword argument from `av.open()` that
faster-whisper's audio decoder still passes, so a fresh `pip install` resolves a
combination that raises `TypeError` on every real transcription. Found while running the
real-model test, not hypothetically. Bisected with prebuilt wheels (18.1.0 works, 19.0.2
does not) and pinned `av<19` in `pyproject.toml`'s `transcription` extra, with a comment
explaining why. Revisit the pin once faster-whisper ships its own upper bound or adapts
to the new PyAV API.

### Known limitations (Phase 4)

- No confirmation prompt before downloading a Whisper model on first use — PLAN.md's
  original risk mitigation ("user-confirmed download only") was not implemented; the
  model downloads silently via Hugging Face Hub's cache on first `transcribe()` call.
  Needs a human product decision on whether/how to prompt (and whether offline-first
  users should be blocked entirely without one).
- No SQLite/FTS5 cross-session transcript search (ARCHITECTURE.md's original Phase 4
  sketch) — transcripts are per-session files only, read directly for the viewer. Revisit
  if/when a library/search UI is scoped.
- Progress reporting is coarse: one update per whisper-internal segment, not a smooth
  percentage: faster-whisper does not expose finer-grained progress than "a segment just
  finished," and total duration is only known once decoding starts.
- The transcript dialog is modal (`QDialog.exec()`), consistent with `consent_dialog.py`'s
  existing pattern, but blocks the tray menu while open — acceptable for a short viewer,
  worth revisiting if transcripts get long enough that users want to keep recording while
  reading one.

## Phase 4.5 — Application Settings and User Configuration (session 8)

| Task | Status | Notes |
|---|---|---|
| `settings/models.py` (`AppSettings`, `TranscriptionProviderId`, `ProviderAvailability`) | DONE | Typed, versioned (`schema_version`), every field defaulted |
| `settings/service.py` (`SettingsService`) | DONE | Atomic JSON read/write (same temp-file-then-rename pattern as `SessionStore`), falls back to defaults on a corrupt/invalid file rather than crashing; no Qt import — framework-independent |
| `storage/paths.py` configurable recordings directory | DONE | `StoragePaths` is no longer frozen; `set_recordings_dir_override()` validates (exists-or-creatable, writable — `validate_writable_directory()`) before accepting, and keeps every previous location in `additional_recordings_dirs` so changing the setting never hides old recordings. `SessionStore.list_sessions()`/`session_dir()` updated to scan/resolve across all known roots — the *existing storage abstraction*, extended, not replaced |
| `infrastructure/credentials/` (`CredentialStore` protocol, `MacOSKeychainStore`, `UnavailableCredentialStore`, `InMemoryCredentialStore`, `mask_secret`) | DONE | Uses the `keyring` package (ctypes bindings to Security.framework) rather than shelling out to the `security` CLI, because `security add-generic-password -w <secret>` briefly exposes the secret on argv (visible to `ps`) — a real security tradeoff, not a style choice; see decision D14 in `.claude/project-memory.md`. Verified against the real macOS Keychain once during this session (set/get/delete a test-only key, immediately cleaned up); all automated tests use `InMemoryCredentialStore` or a mocked `keyring` module, never the real Keychain |
| `transcription/providers/` (`protocol.py`, `http_base.py`, `openai_client.py`, `openrouter_client.py`, `factory.py`) | DONE | Both OpenAI and OpenRouter have real, documented audio-transcription APIs (verified via a research pass before implementing, not assumed) and are fully implemented — neither needed a "coming soon" placeholder. Stdlib `urllib` only, no new HTTP dependency; both request `response_format=verbose_json` explicitly (the default plain-text response has no timestamps). `build_provider_client()` raises a typed, distinguishable `TranscriptionError(code="missing_api_key")` for a cloud provider with no key configured — `TranscriptionManager` turns that into an ordinary transcription failure, never a fatal error, never touching the recording |
| `ui/settings_dialog.py` (`SettingsDialog`, `SettingsDialogContext`) | DONE | General (recording directory + Browse), Transcription (provider dropdown showing live availability, local Whisper model, masked API key with Save/Remove, Test Connection), About. Every change applies immediately (no Save button), matching macOS's own System Settings panes; API key needs an explicit Save so a half-typed key is never persisted |
| `ui/tray.py` Settings menu item + clickable notifications | DONE | "Settings…" item; a missing-API-key transcription failure's tray notification is clickable and opens Settings directly (`QSystemTrayIcon.messageClicked` → `TrayController.notification_clicked`) |
| `app.py` wiring | DONE | Builds `SettingsService`, `default_credential_store()`, and applies a persisted recordings-directory choice at startup via `apply_recordings_dir_override()` — a startup failure (e.g. an unmounted external drive) is caught and shown as a tray notification, never crashes the app, and the default location is used for that session |
| Tests | DONE | `tests/unit/settings/test_service.py`, `tests/unit/infrastructure/test_credentials.py`, `tests/unit/transcription/test_providers.py`, `tests/unit/ui/test_settings_dialog.py`, plus extensions to `tests/unit/test_storage.py`, `test_tray.py`, `test_app_controller.py` — 65 new tests, all using fakes (`InMemoryCredentialStore`, a fake HTTP opener, a mocked `keyring`) — no real API keys, no real network calls, no real Keychain I/O in the automated suite |
| Full suite green | DONE | `bash scripts/test.sh`: 294 tests (216 at the end of Phase 4 + 71 Settings/credentials/providers + 7 from the OpenAI model selector below), ruff + mypy clean; repeated 3x with no flakiness; real `Application` + `SettingsDialog` construction smoke-tested end to end (not just unit tests) |

### Known limitations (Phase 4.5)

- `OpenRouterTranscriptionClient` sends `timestamp_granularities` as a single
  `"segment"` multipart form field; OpenRouter's documentation shows this as a JSON
  array in a JSON request body, and the exact multipart form encoding for an array
  value was not verified against a live OpenRouter account (no API key available in
  this environment). Worth confirming against a real key before relying on it in
  production — see the module docstring in `openrouter_client.py`.
- No automated test exercises the real OpenAI/OpenRouter endpoints or the real macOS
  Keychain (by design — "do not require real API keys in the test suite"); only a
  one-off manual Keychain smoke test was run and immediately cleaned up. A human with
  real API keys should run a manual end-to-end check before relying on either cloud
  provider for real meetings.
- The Settings window has no "coming soon" provider to demonstrate that state, since
  both cloud providers turned out to be genuinely implementable — `ProviderAvailability.COMING_SOON`
  exists in the enum and is handled by the UI, but nothing currently returns it.
- Changing the recordings directory takes effect immediately for new recordings in the
  running app, and is persisted for next launch; there is no migration/move-old-
  recordings-to-new-location option, matching the spec's explicit "never automatically
  move or delete existing recordings."

### OpenAI model selector (added after initial Phase 4.5 report, same session)

User asked for a model picker (e.g. "gpt-4o mini") and the cheapest available option.
Added `OPENAI_AVAILABLE_MODELS` (`openai_client.py`) and a combo box in the Settings
Transcription tab: `whisper-1` ($0.006/min, stays the default), `gpt-4o-mini-transcribe`
($0.003/min, cheapest), `gpt-4o-transcribe` ($0.006/min, most accurate) — prices verified
via web search, not assumed (OpenAI has announced all three retire 2027-02-26).

Verified via a second search before implementing: `gpt-4o-transcribe`/
`gpt-4o-mini-transcribe` do **not** support `response_format=verbose_json` — only
`whisper-1` does. The original `HttpTranscriptionClientBase` always requested
`verbose_json` unconditionally, which would have broken (or silently lost segment
timestamps for) the cheaper models the moment they were exposed as options. Fixed before
shipping: `segment_capable_models` (per-client, per-model) picks `json` for models that
don't support `verbose_json`, and `_parse_transcription_response()` falls back to one
segment spanning the locally-measured (via the `wave` module) audio duration when the
response is plain `{"text": ...}` with no segments/duration/language. `whisper-1` stays
the default specifically because it is the only OpenAI model with real per-segment
timestamps, which the rest of this app (transcript viewer, `Transcript` schema) assumes.
7 new tests (294 total). Known gap, unchanged from before: OpenRouter's model/format
behavior per model was not re-verified in this pass (out of scope — the user asked about
OpenAI).

## Phase 5 — Source-Separated Transcription (Parts A+B, session 9)

User's spec had four parts (A: verify audio-source separation, B: source-separated
transcription, plus two unlisted objectives — configurable summary models and speaker
diarization — whose detailed requirements were never sent). Implemented exactly what was
specified (Parts A+B); the rest is listed under "Not yet started" below.

### Part A — Verification (required before any implementation; done first)

**Finding: audio is already captured as genuinely separate tracks, not mixed.** Verified
three ways, in this order:

1. **Source code**: `native/macos/Sources/main.swift` creates two independent
   `AVAssetWriterInput` objects (`systemInput` line 277, `micInput` line 278), each fed
   by its own callback (`handleAudio(_:input:isMic:)`, line 568) — never merged before
   the writer.
2. **Protocol docs**: `native/macos/PROTOCOL.md`'s `started` event documents
   `tracks: [{index, kind, role?, codec}]`; `main.swift` lines 370/373 assign
   `index: 1, role: "system"` and `index: 2, role: "microphone"` literally.
3. **A real hardware recording** (`LMR_HW_TESTS=1 pytest tests/capture -s -q`, run twice
   this session — permissions are now granted to this shell, unlike earlier sessions):
   `ffprobe` on the resulting `.mov` shows 3 streams — video (0), and **two independent
   AAC audio streams** (1, 2) with their own `start_pts`/`duration`, stream 2 starting
   ~0.09s after stream 1 (matching the real acoustic-coupling delay the test itself
   reports as `mic_offset_s`). Not a single mixed stream.

Conclusion: no Swift/protocol changes were needed for Parts A/B — proceeded straight to
"if separate tracks already exist, implement independent extraction."
`session.json`'s existing `audio_tracks: list[AudioTrackInfo]` (Phase 2/3, not new)
already records the index→role mapping per session, so no new capture-side persistence
was needed either.

### Part B — Source-separated transcription

| Task | Status | Notes |
|---|---|---|
| `media/audio_extractor.py`: `extract_track_wav16k_mono()` | DONE | Extracts exactly one ffprobe stream (`-map 0:<index>`, no `amix`) to 16 kHz mono WAV — no mixing. Shares validation/ffmpeg-invocation code with the existing `extract_wav16k_mono()` (Phase 4's mixdown), via refactored `_probe_audio_streams`/`_run_ffmpeg` helpers |
| `transcription/models.py`: `AudioSource` enum, `TranscriptSegment.source` | DONE | `source: AudioSource \| None = None` — `None` for a Phase-4-style combined transcript (fully backward compatible: old `transcript.json` files with no `source` key load fine). `Transcript.text` prefixes `[System]`/`[Mic]` per line when segments are tagged |
| `transcription/models.py`: `Transcript.source_errors` | DONE | `dict[AudioSource, str]`, populated only for a partial result |
| `domain/session_metadata.py`: `TranscriptionStatus.PARTIAL`, `mark_transcription_partial()` | DONE | One source succeeded, at least one other failed — additive enum value |
| `transcription/manager.py`: rewritten `_run()` | DONE | Branches on `metadata.audio_tracks`: both system+mic tracks present → `_run_source_separated()` (extracts and transcribes each independently, **same engine instance reused for both** — "reuse the existing transcription engine" — merges segments sorted by `start_s` onto one timeline, source-tagged); otherwise → `_run_mixed()` (Phase 4's original single-pass behavior, unchanged, used when mic capture was disabled or for legacy sessions with no track metadata). One source failing never fails the other — job finishes `partial`, not `failed`, when at least one source succeeded; both failing still finishes `failed` exactly as before, and the recording's own `status` is never touched either way |
| UI: `ui/tray.py`, `ui/app_controller.py`, `ui/transcript_dialog.py` | DONE | Tray suffix "Transcribed (partial)"; a partial transcript opens the viewer (not Finder) same as done/failed; the viewer shows which source failed and why, with a Retry button (retries both sources — see limitation below) |
| Tests | DONE | `tests/integration/media/test_audio_extractor.py` (+3, real ffmpeg: isolates one stream without mixing, unknown index, video-only), `tests/unit/transcription/test_models.py` (+6: source field, prefixed text, JSON round-trip, backward-compat load, overlapping cross-source segments allowed), `tests/unit/transcription/test_manager.py` (+8: mixed-pipeline fallback ×2, extraction order, timeline merge, partial-on-extraction-failure, partial-on-transcription-failure, both-fail, engine-reuse), `tests/unit/ui/{test_tray,test_app_controller}.py` (+3: partial suffix, partial routing). 313 tests total |
| **Real end-to-end verification (not just unit tests)** | DONE | Ran the actual hardware test twice this session (permissions now granted); manually extracted both tracks from the real `.mov` with `AudioExtractor.extract_track_wav16k_mono` (confirmed genuinely different byte content, not a coincidental duplicate); ran the full `TranscriptionManager` source-separated pipeline against that real file with the real local `tiny` Whisper model end to end — completed with `transcript_status=done`, `source_errors={}`. (Zero segments is correct: the test recording is a synthesized tone, not speech — Whisper correctly found no speech to transcribe.) |

### Known limitations (Phase 5 Parts A/B)

- **Retry re-runs both sources**, not just the one that failed (same tradeoff already
  documented for Phase 4's single-source retry) — a partial result's retry button calls
  `transcribe_async()` again, which redoes extraction+transcription for system *and*
  microphone, even though one already succeeded. Partial-retry-of-just-the-failed-source
  would need per-source status persisted in `session.json`, not just the transcript file;
  judged out of scope for this pass.
- Diarization (objective 3) and configurable summary model selection (objective 1) were
  both implemented in session 10 — see Phases 5C/5E below. This bullet is stale; kept
  for history.
- Progress reporting during a source-separated job can appear to "restart" partway
  through (the total duration shown jumps from the system track's duration to the
  microphone track's once the second source starts) — same coarse, non-monotonic
  progress UX already noted as a Phase 4 limitation, now also visible across the two
  sources of one job.

## Phase 5C — Speaker Diarization (session 10)

Implemented, found already mostly built but undocumented and uncommitted at the start
of this session (`speaker/`, plus diarization wiring in `transcription/manager.py`,
`ui/app_controller.py`, `ui/tray.py`, `ui/settings_dialog.py`) — a prior session had
written it without updating this file, `CHANGELOG.md`, or `.claude/project-memory.md`,
and left two `ruff` F401 errors uncommitted. An engineering audit was run first (see the
session log entry below) to establish ground truth before accepting or changing any of
it; this section documents the result of that audit plus the fixes applied afterward.

| Task | Status | Notes |
|---|---|---|
| `speaker/protocol.py`, `pyannote_engine.py`, `assignment.py`, `factory.py` | DONE | `pyannote/speaker-diarization-3.1` via `pyannote.audio` (lazy-loaded, same pattern as `TranscriptionEngine`); HF token stored through the existing `CredentialStore` (Keychain), key `"huggingface_token"` |
| `transcription/manager.py`: `diarize_async`/`_run_diarization` | DONE | Independent, retriable background job that re-reads an already-written transcript and only updates system-segment `speaker_id`s; a diarization failure never touches the transcript text/timing |
| `ui/settings_dialog.py` Speakers tab | DONE | Enable checkbox, HF token field; mic is always "You", never diarized |
| **Bug found and fixed**: diarization silently tagged zero segments on single-track (mixed-pipeline) recordings | DONE | `TranscriptionManager._run_mixed()` built segments with `source=None` even when the recording's one audio track had a known role (e.g. microphone capture disabled → system-only). `_run_diarization` filters on `source is AudioSource.SYSTEM`, so it found nothing to tag yet still reported `diarization_status=DONE`. Fixed: `_single_known_source()` now tags a lone system/microphone track's segments with that source (and assigns the fixed `USER_SPEAKER_ID` for a lone mic track), and `_run_diarization` now raises a clean `DiarizationError` ("no system-audio segments to diarize") instead of reporting success when nothing was eligible — verified never-done-with-zero-segments via `test_diarization_fails_cleanly_when_no_segments_are_eligible`. Genuinely ambiguous multi-track cases (unrecognized role) still correctly stay unattributed, not fabricated. |
| **Race found and fixed**: retry-transcription vs. in-flight diarization | DONE | `_run_diarization` read the transcript once at job start and wrote back a merge of it after the (slow) pyannote pipeline ran, with no coordination against a concurrent `transcribe_async` retry — a lost-update race. Fixed with a `revision: int` field on `Transcript` (bumped on every new transcription write) plus a per-session lock (`TranscriptionManager._session_lock`) guarding the read-check-write: if the transcript's revision changed while diarization was computing, the job now fails cleanly ("the transcript changed while diarization was running") instead of overwriting the newer result. Reproduced deterministically in `test_retry_transcription_during_diarization_does_not_corrupt_the_transcript` using a blocking fake diarization engine. |
| Processing order: diarization before summarization | DONE | See Phase 5D below |
| HF token error messages distinguish cause | DONE | `pyannote_engine.py`'s `_load_failure_as_diarization_error` classifies `GatedRepoError` (terms not accepted) / `RepositoryNotFoundError`/401-403 `HfHubHTTPError` (invalid or revoked token) / `requests` connection errors (network) into distinct, actionable messages instead of one generic failure. Best-effort: the exact `huggingface_hub` exception shapes were not verified against a live account with a real invalid/revoked token (none was available) — see module docstring. |
| Download confirmation before first model download | DONE | Enabling diarization in Settings for the first time shows a confirm/cancel dialog naming the model and its approximate size before `diarization_model_download_confirmed` is set; re-enabling later does not ask again |
| "Partially identified" status | DONE | `SessionMetadata.post_processing.diarization_has_unknown_speaker` (set when at least one system segment couldn't be matched to a clustered speaker); tray shows "speakers partially identified" instead of implying every voice was resolved |
| Tests | DONE | `tests/unit/speaker/` (16→23, +7 for the HF-error-classification branches), `tests/unit/transcription/test_manager.py` (+10 for the single-track/race/incomplete-attribution fixes), `tests/unit/ui/test_settings_dialog.py` (+3 for the download confirmation), `tests/unit/ui/test_tray.py` (+2 for partial-identification suffix) |
| **Real-model verification** | TODO | `tests/speaker/test_real_pyannote.py` added this session, gated by `LMR_DIARIZATION_TESTS=1` + a Hugging Face token — **not run** (no token available in this environment). Diarization accuracy against a genuine multi-speaker recording remains completely unverified; do not claim it works accurately until that test (or a manual check) actually runs. |

## Phase 5D — Processing Order (session 10)

| Task | Status | Notes |
|---|---|---|
| Transcription → Diarization → Summarization sequencing | DONE | Previously, `AppController._on_transcription_completed` started diarization and summarization in parallel — since diarization (model load + inference) is far slower than a summary job's near-instant transcript read, summaries almost never actually included speaker-attributed text even when diarization was enabled and later succeeded. Fixed: `_maybe_start_diarization` now returns whether a job actually started; summarization is deferred to `_on_diarization_completed`/`_on_diarization_failed` when one did, and started immediately otherwise (disabled, or `diarize_async` declined to run) |
| Diarization failure doesn't block summarization | DONE | `_on_diarization_failed` also calls `_maybe_start_summary` — a diarization failure leaves the transcript untouched and summarization still proceeds with whatever transcript text exists, never left waiting on a job that will never call back |
| Tests | DONE | `tests/unit/ui/test_app_controller.py` (+4: waits for diarization completion before summarizing, still summarizes after diarization failure, summarizes immediately when diarization disabled, summarizes immediately when `diarize_async` declines) |

## Phase 5E — AI Meeting Summarization (session 10)

Implemented, found already mostly built but undocumented and uncommitted at the start of
this session (`summarization/`, `ui/summary_dialog.py`, `ui/summary_bridge.py`) — same
situation as Phase 5C above. PLAN.md's original Phase 5 roadmap (`summarization/
ollama_client.py`, structured schema, prompt hardening) is now implemented, not future.

| Task | Status | Notes |
|---|---|---|
| `summarization/manager.py` (`SummaryManager`) | DONE | Background job per session, triggered after transcription (and diarization, if enabled — Phase 5D); never touches the transcript or recording on failure |
| `summarization/models.py`, `schema.py`, `store.py` | DONE | `MeetingSummary`/`Decision`/`ActionItem`, shared JSON schema + system prompt, append-only `summary.json` (every version preserved) |
| Providers: Ollama (local), OpenAI, OpenRouter | DONE | Independent provider/model selection from the transcription settings (separate `SummaryProviderId`); OpenAI/OpenRouter reuse the same API-key credential as the transcription provider of the same name (one account, not two key prompts) |
| `ui/settings_dialog.py` "AI Summary" tab | DONE | Enable checkbox, provider dropdown, Ollama server URL + model, OpenAI model picker, OpenRouter model field, API key row, Test Connection |
| **Security gap found and fixed**: no prompt-injection hardening | DONE | PLAN.md's own Phase 5 roadmap listed "transcript delimited, no tool use" as a requirement; it had not been implemented — transcript text went straight into the user message with no data/instruction boundary. Fixed: `summarization/schema.py` now wraps the transcript in `<meeting_transcript>...</meeting_transcript>` tags (stripped from the transcript text itself first, so spoken words can't forge a fake closing tag), and the system prompt explicitly instructs the model never to follow instructions found inside that boundary. Verified via `test_system_prompt_establishes_the_transcript_as_untrusted_data` and provider-level tests asserting the delimiters actually appear in the request body sent to Ollama/OpenAI. No tool use or agentic capability was ever present or added. |
| **Gap found and fixed**: Ollama "Test Connection" never checked the model existed | DONE | It only hit `/api/tags` to confirm the server was reachable — a reachable server with a never-pulled model name reported "Connected successfully" and only failed later, during a real summary job. Fixed: `OllamaSummaryClient.test_connection()` now also checks the configured model against the server's installed-model list (tolerant of a missing `:tag` suffix) and returns a distinct "model not installed, run `ollama pull <model>`" message, separate from a network-reachability failure |
| **Gap found and fixed**: missing-API-key summary failures were silent | DONE | `_on_summary_failed` only logged; a transcription failure for the same reason already showed a clickable tray notification. Fixed: summary failures now show the same kind of notification, and clicking it opens Settings directly on the relevant tab (`show_settings_dialog`/`SettingsDialog` gained `initial_tab`, mapped from a `_last_notification_settings_tab` field replacing the old boolean flag) |
| **Gap found and fixed**: unbounded raw provider error bodies in logs | DONE | A provider's HTTP error response body was embedded in full into the raised exception message (and therefore logged) with no length limit. Fixed: `domain/errors.py`'s new `sanitize_error_detail()` collapses whitespace/newlines and truncates to 300 characters, applied in both the summarization and transcription HTTP provider clients' error paths |
| Tests | DONE | `tests/unit/summarization/` (+~20 across `test_schema.py`, `test_providers.py`, `test_manager.py`), `tests/unit/test_errors.py` (new, 3 tests for `sanitize_error_detail`) |
| **Real-provider verification** | TODO | `tests/summarization/test_real_ollama.py`/`test_real_openai.py`/`test_real_openrouter.py` added this session, gated by `LMR_OLLAMA_TESTS=1`/`LMR_OPENAI_SUMMARY_TESTS=1`/`LMR_OPENROUTER_SUMMARY_TESTS=1` respectively — **none run** (no local Ollama server with a pulled model, no API keys available in this environment). Every summary provider client is verified only against a fake HTTP transport; real-provider behavior (especially OpenRouter's request shape, already flagged as unverified for transcription in Phase 4.5) remains unconfirmed. |

### Known limitations (Phase 5E)

- No automatic retry on a transient provider failure (timeout, momentary 503) — one
  attempt per job, same as the pre-existing transcription-provider pattern; "retry" means
  the user clicks Regenerate.
- No per-decision/action-item evidence beyond `segment_refs` indices (Phase 5F) — no
  confidence score, no verification that the model's citation is actually correct beyond
  trusting the structured-output contract.
- `OpenRouterSummaryClient`'s request shape has not been verified against a live
  OpenRouter account (same unverified status already documented for the transcription
  client in Phase 4.5).

## Phase 5F — Summary Markdown Export + Transcript Evidence References (session 10)

| Task | Status | Notes |
|---|---|---|
| `summarization/markdown.py` (`render_summary_markdown`) | DONE | Renders the *latest* summary as `summary.md` — title (falls back to session title/id), date, provider/model, executive summary, topics, decisions, action items (owner/deadline), risks, follow-ups, speaker list (when a transcript is available). Pure rendering, no I/O; never invents content |
| `summarization/store.py`: `write_markdown()` | DONE | Atomic write (same temp-file-then-rename pattern as every other store in this project), always overwrites with the latest rendering — `summary.json` remains the versioned history, `summary.md` is a convenience export of the current version only |
| `SummaryManager._run()` wiring | DONE | Renders and writes `summary.md` right after `append_version()` succeeds; a markdown-rendering/write failure is logged and isolated — it never turns an otherwise-successful summary job into a reported failure |
| `Decision.segment_refs`, `ActionItem.segment_refs` | DONE | `list[int]`, additive (defaults to `[]`), indices into `Transcript.segments` that support a decision/action item, when the model could identify them. Old `summary.json` files with neither key present still load (verified in `test_store.py`) |
| `Transcript.text_with_segment_indices` | DONE | Like the existing `.text` property, but each rendered line is prefixed with its real position in `segments` (e.g. `"[7] Speaker 1: ..."`) — this, not the plain `.text`, is what's sent to a summary provider now, so `segment_refs` indices are resolvable back to a real segment/timestamp |
| `summarization/schema.py` | DONE | `SUMMARY_JSON_SCHEMA` requires `segment_refs` on every decision/action item (an empty list is valid — the system prompt explicitly says never invent or guess an index) |
| `summary.md`'s evidence references | DONE | Each decision/action item with a resolvable `segment_refs` entry gets a `_(see 00:42)_`-style timestamp suffix; an out-of-range or unresolvable reference is silently dropped, never fabricated |
| Tests | DONE | `tests/unit/summarization/test_markdown.py` (new, 8 tests), `tests/unit/summarization/test_manager.py` (+2: markdown written on success, markdown failure doesn't block completion), `tests/unit/summarization/test_store.py` (+3: write/overwrite, backward-compat load), `tests/unit/transcription/test_models.py` (+2 for `text_with_segment_indices`) |

### Known limitations (Phase 5F)

- No per-meeting folder restructuring was done — storage stays flat
  (`recordings/<session_id>/{recording.mov,session.json,transcript.json,transcript.txt,
  summary.json,summary.md}`), per the explicit "keep the current filesystem
  architecture, do not introduce a database or migrate existing folders" instruction for
  this pass. A year/month hierarchy or per-artifact subfolders were considered and
  deliberately not built.
- `segment_refs` accuracy depends entirely on the summary model correctly citing line
  numbers from `text_with_segment_indices` — never independently verified against the
  transcript's actual content beyond resolvability (does the index exist), not relevance
  (does that segment actually support the claim).

## Phase 6 — Packaging (started session 6)

| Task | Status | Notes |
|---|---|---|
| PyInstaller spec (`packaging/macos/LocalMeetingRecorder.spec`) | DONE | onedir -> `.app` BUNDLE, icon, `Info.plist` usage-description keys matching the dev wrapper |
| `app.entitlements` / `helper.entitlements` | DONE | audio-input + unsigned-memory/library-validation for Python/Qt; real Developer ID signing still needs these applied with a real identity |
| `scripts/build_macos.sh` | DONE | builds helper, renders icon, runs PyInstaller, copies helper into `Contents/Helpers/`, ad-hoc signs both (`codesign --sign -`) |
| Build executed and verified | DONE | clean PyInstaller build; `codesign --verify --verbose=2` → "valid on disk" + "satisfies its Designated Requirement"; frozen app correctly refused a second launch while a dev-mode instance held the single-instance lock, proving `resolve_helper_binary()`'s `bundle_contents_dir()` path and the lock guard both work inside the frozen build |
| Gatekeeper-clean launch (no "unidentified developer" prompt) | TODO | `spctl --assess` rejects the ad-hoc-signed build (expected, no Developer ID) — needs Apple Developer Program enrollment, open question since session 1 |
| Notarization | TODO | blocked on the same Developer ID account |
| `tests/packaging/` (`LMR_PACKAGING_TESTS=1`) | TODO | no automated test exercises the built `.app` yet |

## Phase 6B — CPU-First, Low-Resource and Cross-Platform Readiness (session 11)

Product requirement: run on an ordinary Windows/macOS computer with no NVIDIA GPU, no
CUDA, integrated graphics, an entry-level CPU, and limited RAM. CPU-only is the default
everywhere; GPU support is never a hidden dependency; recording must stay reliable even
when AI processing is slow or disabled.

### Step 1 — GPU dependency audit (done first, before any change)

| Finding | Verdict |
|---|---|
| `faster-whisper`/`ctranslate2` | Already safe. `transcription/engine.py`/`config.py` already defaulted `device="cpu"`, `compute_type="int8"` explicitly before this session — `WhisperModel`'s *own* library default is `device="auto"` (can select CUDA), but this app never relies on that. `ctranslate2`'s PyPI wheel is 6MB here (no bundled CUDA runtime); it only attempts CUDA if explicitly asked, which this app never does. |
| `pyannote.audio`/`torch` | **Gap found and fixed.** No code ever called `pipeline.to(torch.device(...))` — CPU happened to be the practical outcome today (confirmed: pyannote's own `fix_reproducibility()` falls back to `torch.device("cpu")` when `.device` was never set), but this was implicit, not defended. Fixed: `pipeline.to(torch.device("cpu"))` is now called explicitly every time, plus an optional `torch.set_num_threads()` cap. |
| `torch==2.6.0`/`torchaudio==2.6.0` pins | **Real cross-platform gap, documented (not fully fixable from `pyproject.toml` alone).** PyPI's default wheel for these exact pins is CPU/MPS-only on macOS (no issue today), but is the **CUDA-enabled build** on Windows/Linux — multi-gigabyte NVIDIA runtime libraries pulled onto a machine with no GPU at all, the moment someone runs a plain `pip install .[diarization]` on those platforms. Documented in `pyproject.toml` with the exact fix (`--index-url https://download.pytorch.org/whl/cpu`) and wrapped in `scripts/install_diarization_cpu.{sh,ps1}`. |
| FFmpeg | Clean — no `-hwaccel`/GPU encode flags anywhere in `media/`; audio-only extraction. |
| Ollama | Already compliant — optional (`summary_enabled=False` by default), no auto-download of a model (the user must type a model name they've already pulled), not required at startup. |
| PyInstaller | **Real bug found and fixed.** `excludes=[]` meant that if the `diarization` extra happened to be installed in whatever venv built the app (true for this project's own dev venv), PyInstaller's static import analysis would follow `speaker/pyannote_engine.py`'s lazy `import torch`/`import pyannote.audio` into site-packages and bundle the whole tree — verified by actually building the app both ways (see below). |
| Model downloads on first use | Pre-existing, unchanged gap: local Whisper is the default transcription provider and downloads its model (now "base", ~142MB, was "small") on first real transcription with no confirmation prompt — same known limitation documented since Phase 4; diarization already has a confirmation (Phase 5C/session 10), and this session added the same awareness via the Whisper model note label in Settings, but did not add a blocking confirmation dialog to the core auto-transcribe flow (judged out of scope for this pass — see Known limitations). |

### Step 2 — CPU-only local inference

| Task | Status | Notes |
|---|---|---|
| faster-whisper: explicit `device="cpu"`/`compute_type="int8"` | DONE | Already true before this session (verified, not assumed); now also has a regression test proving `torch.cuda.is_available() -> True` has zero effect on the chosen device |
| Configurable Whisper model (tiny/base/small/medium/large-v3) | DONE | Unchanged set of options in Settings; **default changed from "small" to "base"** (`config.py`, `settings/models.py`, `transcription/engine.py`) — "tiny" called out as the low-resource option, "medium"/"large-v3" labeled advanced/slower in the Settings UI note |
| `cpu_threads` (ctranslate2) | DONE | New `TranscriptionEngine(cpu_threads=...)`, wired from `settings/profiles.py`'s per-profile value (`0` = ctranslate2's own default, unchanged unless a profile overrides it) |
| pyannote: explicit CPU, never CUDA | DONE | `pipeline.to(torch.device("cpu"))` always called; see Step 1 |
| pyannote: lazy-loaded | DONE | Unchanged from Phase 5C — importing the module or constructing the engine touches neither `torch` nor `pyannote.audio` until `diarize()` is actually called |
| pyannote: background worker, never blocks recording/UI | DONE | Unchanged from Phase 5C — `diarize_async()` runs on its own `threading.Thread`; recording never goes through the diarization manager at all |
| pyannote: `cpu_threads` cap | DONE | `torch.set_num_threads(n)` called when configured, from the same per-profile value as Whisper's |
| Users can disable diarization entirely | DONE | Unchanged from Phase 5C (`diarization_enabled` toggle); Low Resource profile now additionally force-disables it |
| Warn users diarization may be slow on low-end CPUs | PARTIAL | The Settings UI's diarization note already says identification "runs locally" and the download-confirmation dialog mentions CPU cost implicitly; no explicit "this may take minutes on an older machine" copy was added — judged a small enough gap to leave for a future pass rather than add more UI text this session |
| Ollama kept optional, no startup requirement, no auto-download | DONE | Confirmed unchanged — `summary_enabled=False` default, `app.py` never calls Ollama at startup, the user must type a model name themselves |
| Cloud summarization stays available as an alternative | DONE | Unchanged — OpenAI/OpenRouter summary providers untouched by this phase |

### Step 3 — Low-resource processing profile

| Task | Status | Notes |
|---|---|---|
| `settings/models.py`: `ProcessingProfile` (Low Resource / Balanced / Quality) | DONE | New `AppSettings.processing_profile` field, default `BALANCED` |
| `settings/profiles.py`: profile → behavior lookup | DONE | Pure functions/dataclass, no I/O: `effects_for(profile)` (max concurrent AI jobs, CPU-thread cap, whether diarization is allowed) and `recommended_whisper_model(profile)` |
| Low Resource behavior | DONE | CPU-only (unconditional for every profile, not just this one), Whisper "tiny", `cpu_threads=2`, diarization force-disabled, at most 1 AI job app-wide at a time (`ResourceGovernor`) |
| Balanced behavior (default) | DONE | Whisper "base", diarization available, unlimited concurrent jobs (unchanged pre-Phase-6B behavior) |
| Quality behavior | DONE | Whisper "small", diarization + cloud AI available, unlimited concurrent jobs — still CPU-only, no GPU option added |
| Settings UI: one combo box, not a dozen toggles | DONE | `ui/settings_dialog.py`'s General tab gained a single "Performance" row; changing it immediately applies the recommended Whisper model and (for Low Resource) disables diarization — both still individually overridable afterward via their own existing controls |

### Step 4 — Protecting recording performance

| Task | Status | Notes |
|---|---|---|
| Recording never waits for transcription/diarization/summarization | DONE (pre-existing, re-verified) | Structural: `AppController._maybe_start_transcription` only fires after `RecordingState.SAVED`; recording's own state machine has no AI-related transition at all |
| AI jobs run in background workers | DONE (pre-existing, re-verified) | Unchanged — every job is a `threading.Thread`, never inline on the Qt thread |
| Avoid concurrent heavy AI jobs on Low Resource | DONE | New `infrastructure/resource_governor.py`: an app-wide (not per-session) limiter. Per-session duplicate-prevention already existed and only ever stopped a session from racing *itself* — nothing previously stopped two *different* sessions' jobs from running concurrently. Verified deterministically with real threads (not mocked) in `tests/unit/infrastructure/test_resource_governor.py` and `tests/unit/transcription/test_manager.py` |
| Limit CPU thread usage where supported | DONE | `cpu_threads` wired into both faster-whisper/ctranslate2 and torch (pyannote) — see Step 2 |
| Avoid excessive memory usage | PARTIAL | No explicit memory cap was added (faster-whisper/pyannote don't expose one cleanly); the Low Resource profile's smaller model choice and job serialization are the actual memory-reduction levers. Real peak-RSS numbers were measured (see Step 8) but no hard ceiling is enforced |
| Allow postponing AI processing until recording finishes | DONE (already true by construction) | AI processing cannot start before `RecordingState.SAVED` — recording already cannot be delayed by AI in either direction. No additional "defer transcription until later" feature was built; not requested beyond this guarantee, which already held |
| Processing failures never corrupt original recordings | DONE (pre-existing, re-verified) | Unchanged — every post-processing job only ever reads `recording.mov` via ffmpeg/ffprobe into temp files; none opens it for writing |

### Step 5 — Cross-platform architecture

Windows blockers were inventoried, not implemented, per explicit scope. See **PLAN.md's
Phase 7 section** for the full gap analysis (capture technology fork, permission-model
mismatch, ffmpeg bundling, the `keyring`-based credential-storage shortcut, suggested
phased order). No `capture/windows/` code was written.

### Step 6 — Installation and packaging

| Task | Status | Notes |
|---|---|---|
| CPU-only install works without NVIDIA drivers/CUDA (macOS) | DONE, verified | This project's own dev venv has the `diarization` extra installed; `faster-whisper`/`ctranslate2`/`pyannote.audio` all function correctly with no CUDA toolkit, no NVIDIA GPU, confirmed by the full real test suite passing on this machine (Apple Silicon, no NVIDIA hardware exists on macOS at all) |
| No CUDA toolkit/NVIDIA GPU/CUDA env vars required | DONE | Confirmed by code audit (Step 1) — nothing in this codebase reads a CUDA environment variable or requires a GPU driver |
| PyInstaller does not bundle unnecessary GPU libraries | DONE, verified by an actual build | `packaging/macos/LocalMeetingRecorder.spec` now excludes the diarization dependency tree. **Verified by actually building the app** (`pyinstaller` run directly against the spec, not just read): resulting `.app` is 360MB with zero `torch`/`pyannote` files inside (`find ... -iname "*torch*" -o -iname "*pyannote*"` → 0 matches), versus ~390MB of installed site-packages for that tree alone (`torch` is 342MB by itself) that would otherwise have been pulled in |
| First-time install doesn't download large models unexpectedly | PARTIAL | Diarization already requires an explicit opt-in + confirmation dialog (Phase 5C/session 10). Local Whisper (the default transcription path) still downloads its model on first real transcription with no confirmation — pre-existing, documented gap, judged out of scope for this pass (see Step 1) |
| Models download only when the relevant feature is used | DONE for diarization/cloud; PARTIAL for local Whisper | Same as above — diarization and every cloud provider are fully opt-in; local Whisper is the always-on default and downloads on first use |
| Supported installation paths documented | DONE | README.md's new "Hardware & Performance" section + `scripts/install_diarization_cpu.{sh,ps1}` |

### Step 7 — Automated tests

23 new tests added (433 → 456 total in the default `tests/unit`+`tests/integration` suite):
- `tests/unit/infrastructure/test_resource_governor.py` (new, 5): unlimited-by-default,
  limit-of-1 serializes two concurrent jobs (real threads, not mocked), a crashed job
  releases its slot, `set_limit` takes effect live, raising the limit wakes a waiter
- `tests/unit/settings/test_profiles.py` (new, 4): every profile's effects and
  recommended model
- `tests/unit/speaker/test_pyannote_engine.py` (+5, with real fake-pipeline fixtures
  updated to exercise `.to()`): pipeline explicitly moved to CPU, `cpu_threads` set
  when configured and left alone when not, plus the pre-existing suite updated for the
  new `.to()` call
- `tests/unit/transcription/test_engine.py` (+4): defaults are cpu/int8/base with zero
  configuration, `torch.cuda.is_available() -> True` changes nothing, `cpu_threads`
  defaults to 0 and is forwarded, construction never needs torch/cuda to exist
- `tests/unit/transcription/test_manager.py` (+2): a shared `ResourceGovernor(limit=1)`
  actually serializes two different sessions' transcription jobs; without one, the
  default (unlimited) behavior is unchanged
- `tests/unit/ui/test_settings_dialog.py` (+5): default profile is Balanced/base, Low
  Resource sets tiny + disables diarization + sets the governor limit to 1, Quality
  allows diarization + unlimited, switching away from Low Resource does not
  auto-re-enable diarization

**Explicitly not claimed from mocked tests:** none of the above claims real-world CPU
performance — that is Step 8's job, with real (if partial) measurements.

### Step 8 — Benchmarking

`scripts/benchmark_cpu.py` (new): a reproducible, dependency-free (stdlib `resource` for
peak RSS/CPU-time; optional `psutil` for a live CPU% sample, never required) CLI that
measures transcription, diarization, and summarization separately and reports hardware
info (CPU model, RAM, OS) alongside each stage. It never fabricates a number for a stage
it wasn't asked to run — see its `"not_run"` list.

**Actually run this session** (Apple M2 Max, 12 logical CPUs, 32 GB RAM, macOS 26.2
arm64 — real hardware, not estimated):

| Duration | Model | Device/compute | Wall time | CPU time | Peak RSS |
|---|---|---|---|---|---|
| 71.6 s (synthesized speech) | tiny | cpu/int8 | 9.9 s | 43.9 s | 478.6 MB |
| 71.6 s (synthesized speech) | base | cpu/int8 | 13.8 s | 62.8 s | 697.5 MB |
| 352.4 s (~5.9 min, synthesized speech) | tiny | cpu/int8 | 45.0 s | 214.5 s | 739.9 MB |

**Not run, reported honestly rather than estimated:** 30-minute and 60-minute
transcription runs (no real recording of that length was available in this session, and
synthesizing+running one was judged too time-consuming for this pass); diarization
timing (no Hugging Face token available); summarization timing (no local Ollama server
or cloud API key available). Run `scripts/benchmark_cpu.py --diarize`/
`--summarize-transcript` yourself with real credentials to fill these in — see
TESTING.md's results log for the exact commands.

### Known limitations (Phase 6B)

- Local Whisper's first-use model download has no confirmation prompt (pre-existing
  gap, unchanged — diarization's equivalent confirmation was already built in session
  10 and is unaffected).
- No explicit "diarization may take minutes on an older CPU" warning text was added
  beyond what already exists.
- No hard memory ceiling is enforced on Low Resource — model choice and job
  serialization are the actual levers; a determined user could still run a large model
  on a low-RAM machine and exhaust memory.
- 30/60-minute transcription benchmarks, and any diarization/summarization benchmark,
  were not run in this session — see Step 8.
- The `torch==2.6.0`/`torchaudio==2.6.0` CPU-wheel problem on Windows/Linux is
  documented and wrapped in an install script, but cannot be fixed declaratively from
  `pyproject.toml` alone — this is a real `pip`/PyPI ecosystem limitation, not an
  oversight.
- `scripts/install_diarization_cpu.ps1` (Windows) has not been run on a real Windows
  machine — no such machine is available in this environment; it was written from
  documented `pip`/PyTorch behavior, not verified end to end.

## Phase 6C — Standalone DMG Installer (session 12)

Goal: a double-click-from-Finder install, no PyCharm/Terminal/venv activation needed,
in `/Applications`, with the tray, recording, transcription, and settings all working.

| Task | Status | Notes |
|---|---|---|
| `scripts/build_dmg.sh` (new) | DONE | Runs `scripts/build_macos.sh` unchanged, then stages the `.app` + an `Applications` symlink and runs `hdiutil create` for a standard drag-to-install DMG. Refuses to proceed if it finds developer-only files (`.env`, `.git`, `pyproject.toml`, anything named like a secret/credential) or an actual private key (`.pem`/`.key` content-checked for a `PRIVATE KEY` marker, not just the filename — the first version of this check false-positived on certifi's public `cacert.pem`, fixed) inside the built bundle. |
| **Bug found and fixed**: the Phase 6B GPU-exclusion list was incomplete | DONE | Actually building the app (not just reading the spec) surfaced `matplotlib`/`pandas`/`scipy`/`Pillow`/`scikit-learn`/`SQLAlchemy`/`alembic`/`optuna` inside `Contents/Frameworks` — all transitively pulled in by `pyannote.metrics`/`pyannote.database`/`pyannote.pipeline`/`speechbrain`, none sharing a name with "torch"/"pyannote", so Phase 6B's grep-based verification missed them even though its *intent* (exclude everything diarization-only) was correct. Traced each one's `Required-by` via `pip show` before excluding — confirmed none are needed by faster-whisper/PySide6/pydantic/keyring. |
| **Bug found and fixed**: `mypy`/`pytest` were being bundled | DONE | `pydantic.mypy` (pydantic's own optional mypy-plugin submodule) statically imports the real `mypy` package, and `fsspec` (a huggingface_hub dependency) ships a `conftest.py` that imports `pytest`/`pluggy`/`iniconfig` — both tools are declared only in this project's own `[dev]` extra. Found in the PyInstaller `Analysis` warnings log, not guessed; excluded. |
| **Bug found and fixed**: `build_macos.sh`'s `pip install ".[packaging]"` silently de-editable-izes the dev venv | DONE | Missing `-e` meant running the packaging build once replaced the editable dev install with a frozen copy in `site-packages` — which broke `storage.paths.project_root()`'s dev-mode fallback (`resolve_helper_binary()` started returning `None` for the dev helper path) for the rest of that shell session. Found by hitting it directly: the project's own `tests/capture` hardware test started skipping with "not built" even though the dev helper clearly existed on disk. Fixed by adding `-e`; re-verified the dev environment and full test suite afterward. |
| **Bug found and fixed**: `ffmpeg`/`ffprobe` were not resolvable from a double-clicked app | DONE | Verified directly: a GUI app launched via `open`/double-click gets a minimal LaunchServices `PATH` (`/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.` on this machine) that excludes Homebrew's install prefix (`/opt/homebrew/bin` on Apple Silicon) — confirmed `shutil.which("ffmpeg")` returns `None` under that exact `PATH`. `AudioExtractor`/`MediaInspector` previously passed `"ffmpeg"`/`"ffprobe"` straight through to subprocess calls, which would have silently broken transcription (not recording itself — capture never touches ffmpeg) for every double-click launch. Fixed with `storage.paths.resolve_external_binary()`: checks `PATH` first, then common install locations (Homebrew Apple Silicon/Intel, MacPorts), falls back to the bare name unchanged if nothing is found; an already-explicit path is never second-guessed. Re-verified the fix under a real minimal `PATH` via `env -i`: extraction and a real "tiny"-model transcription both succeeded end to end. |
| Final bundle size | DONE, measured | 260MB `.app` (down from 360MB in Phase 6B, which still carried the undetected matplotlib/pandas/scipy/sklearn/sqlalchemy/alembic/optuna tree), 106MB compressed `.dmg`. Zero matches for the full diarization+dev-tool exclude list inside the built bundle, verified with `find`, not assumed. |
| App bundle self-containment | DONE, verified | PyInstaller bundles the Python runtime, PySide6/Qt, and every pure-Python dependency; the Swift helper is copied into `Contents/Helpers/` and ad-hoc signed separately. The one pre-existing gap found and fixed this session was the `ffmpeg`/`ffprobe` `PATH` issue above — everything else the app needs is genuinely inside the bundle. |
| Real launch test: no PyCharm, no Terminal, no manual venv activation | DONE, verified | Installed via `cp -R` from the mounted DMG into `/Applications` (the DMG's own drag-and-drop UX), then launched with `open /Applications/LocalMeetingRecorder.app` (the same call Finder makes on a double-click) from a shell that never sourced `.venv/bin/activate`. Process came up and stayed running (`ps`/`pgrep` confirmed); `lsappinfo` showed `type="UIElement"` (matching `LSUIElement=true`) with a normal ~1.5s launch-to-checkin time, consistent with the tray icon having been created — this session's sandboxed shell has no Accessibility access to literally screenshot the menu bar and confirm the icon's pixels, so that one piece is inferred from window-server registration, not visually confirmed. |
| Recording start/stop, system audio, microphone | DONE for the mechanism; INCONCLUSIVE for one manual sub-test | Permissions were already granted in this shell's responsible-process tree (`screen=granted`, `microphone=granted`, confirmed via a real `probe`). Drove the real `MacOSCaptureBackend` class against the bundled helper end to end: `start` → real ScreenCaptureKit capture → `stop` → `finalized=true`, producing a valid `.mov` with exactly 1 video + 2 audio streams (system role, microphone role), confirmed with `ffprobe`. Microphone audio content was real (non-zero, ambient-noise-level). **System-audio content came back digitally silent** in every one of several manual attempts (direct helper JSON, the real backend class, both the bundled and the dev helper binary, with and without hardened-runtime signing) — but the project's own canonical, pytest-based hardware test (`tests/capture/test_real_capture.py`), run twice in the same session on the same machine, passed cleanly both times with real captured tone audio (`system_rms_db≈-9`). Same helper logic, same signing conclusion ruled out as the cause (tested a hardened-runtime-stripped copy of the exact bundled binary — still silent) — the discrepancy traces to something about this agent session's own ad-hoc script invocation of `afplay`/the helper outside of pytest, not a defect in the packaged app. Treat system-audio capture as proven to work (by the canonical test, using the same mechanism the app uses) but **not independently re-confirmed by this session's manual DMG-specific test** — a human should still do one real click-through test to be certain. |
| Settings/recordings use the correct Application Support directory | DONE, verified | The running packaged app's `app.lock`/`settings.json` appeared in `~/Library/Application Support/LocalMeetingRecorder/` — the same location `default_data_dir()` resolves to for dev-mode runs, confirming shared, correct state regardless of how the app was launched. |
| Transcription works under a double-click's minimal `PATH` | DONE, verified | See the ffmpeg fix above — real extraction + a real "tiny"-model transcription both completed successfully under a reproduced minimal `PATH`. |
| Gatekeeper / distribution | Unchanged, as instructed | Ad-hoc signed only, exactly as asked — `spctl --assess` still rejects it (expected, no Developer ID). A locally built-then-installed app has no `com.apple.quarantine` attribute (confirmed via `xattr`), so Gatekeeper's "unidentified developer" dialog does not even trigger for this exact build-it-yourself-and-install-it workflow; it would trigger if this DMG were downloaded or sent to another Mac. No notarization claim is made. |

### Known limitations (Phase 6C)

- System-audio capture from the packaged app was not independently, conclusively
  re-verified by this session's own manual testing (see above) — rely on the canonical
  hardware test's result plus a human's own real click-through test before trusting it
  fully for the packaged build specifically.
- No custom DMG background image or icon-position layout — a plain, functional
  drag-to-`Applications` window, deliberately not polished further (not asked for,
  avoids unnecessary complexity).
- The DMG itself is not signed (only the `.app` inside it is) — not necessary for local
  installation and not requested.
- `scripts/build_dmg.sh`'s developer-file safety check is a reasonable but not
  exhaustive scan (specific filename/content patterns) — it is a safety net, not a
  guarantee that no other category of unwanted file could ever end up in a bundle.

## Phase 6D — Rebrand + Real-Hardware Permission Fixes (session 13)

Goal (user-driven, following real-machine testing of the Phase 6C DMG): rename the
app to "My Recorder", and fix a real microphone/screen-recording permission bug the
user hit on first real hardware test.

| Task | Status | Notes |
|---|---|---|
| Rebrand to "My Recorder" | DONE, verified | New `APP_DISPLAY_NAME` constant (`__init__.py`), kept deliberately separate from the internal `APP_NAME` (Application Support folder, Keychain service name) so the rebrand cannot orphan existing settings/recordings/credentials. Updated: `.app` bundle filename, `CFBundleDisplayName`/`CFBundleName` (both the main spec and the helper's embedded Info.plist), the real consent-dialog usage-description text users actually see in the system prompt, tray tooltip, "Permission Needed" dialog text, single-instance error messages, DMG volume name. Bundle identifier (`com.localmeetingrecorder.app`) and Application Support folder name deliberately left unchanged. Verified: renamed `.app` launches cleanly via `open`, `lsappinfo` shows "My Recorder" as the process display name, `codesign --verify` passes. |
| **Bug found and fixed**: permission check never actively requested | DONE, verified | `check_permissions()` only ever read cached TCC state — it never called the helper's `request_permissions` command (the only path that calls `AVCaptureDevice.requestAccess`/`CGRequestScreenCaptureAccess`). A fresh install's first Start Recording click went straight from "not determined" to the app's own "go open Settings" guidance, skipping the real OS consent prompt entirely, so System Settings had nothing to toggle — exactly matching what the user reported. Fixed: `RecordingCoordinator.request_permissions()`, `CoordinatorBridge.request_permissions_async()` (new `permissions_requested` signal), and `app_controller.py` now actively request when a permission is `NOT_DETERMINED`, falling back to the guide dialog only if still not granted afterward. 2 new tests. |
| **Bug found and fixed**: helper's own TCC identity broke Screen Recording delegation | DONE, verified on real hardware | Even after the above fix and a confirmed grant in System Settings, Screen Recording kept re-prompting (Microphone worked). Root-caused by reading the real TCC databases directly via `sqlite3` (per-user DB for Microphone; system-wide DB, needs `sudo`, for Screen Recording) rather than trusting the Settings UI: `com.localmeetingrecorder.app` showed `auth_value=2` (granted) for both services, yet the helper subprocess's own Screen Recording check still failed every time. The helper was ad-hoc signed/Info.plist-identified under its own distinct bundle ID (`com.localmeetingrecorder.capture-helper`) — Screen Recording's TCC enforcement apparently does not honor responsible-process delegation from a subprocess the way `AVCaptureDevice`'s microphone check does. Fixed by signing/identifying the helper under the exact same bundle ID as the main app (`scripts/build_helper.sh`'s `codesign --identifier`, `native/macos/Info.plist`'s `CFBundleIdentifier`) — confirmed via `codesign -dv` both now report `com.localmeetingrecorder.app`. |
| Stuck TCC state from ad-hoc rebuilds | Worked around, not eliminated | Even with the matching identifier, the user's existing (now-stale) Screen Recording grant did not start working until `tccutil reset ScreenCapture com.localmeetingrecorder.app` followed by one fresh grant cycle on the final rebuild — ad-hoc (Team-ID-less) signing appears not to reliably carry a TCC grant across rebuilds even when the identifier string is unchanged. A real Developer ID signature (out of scope this session — user asked for ad-hoc only) would likely remove this fragility; see `.claude/skills/packaging/SKILL.md`. |
| Real end-to-end confirmation | DONE | User confirmed Start Recording worked on real hardware (`/Applications/My Recorder.app`) after all three fixes above were applied together. |

### Known limitations (Phase 6D)

- The ad-hoc-signing-related stuck-TCC-state issue is worked around (one `tccutil
  reset` got the user to a working state) but not structurally eliminated — another
  rebuild could in principle trigger the same symptom again until the app has a real
  Developer ID signature.
- Internal Python package/module names, the Application Support folder, and the
  Keychain service name still say "LocalMeetingRecorder" — only the user-facing
  branding was renamed, deliberately, to avoid orphaning existing user data.

## Phase 7

Windows *capture* is now implemented — see Phase 8A below. What's still not started: an
NSIS/MSI installer, `infrastructure/single_instance.py`'s named-mutex branch, and
`WindowsCredentialManagerStore` (a near-free addition — `keyring` already ships a Windows
backend). See PLAN.md's Phase 7 section for the full remaining inventory.

## Phase 8A — Windows Native Capture Backend (session 14)

Goal (explicit user spec): a Windows recording backend matching the macOS one's
capabilities — screen capture, independent system-audio + microphone streams, start/stop
through the existing Python app, output compatible with the existing transcription
pipeline — implemented against the existing `CaptureBackend` protocol with no changes
needed above `capture/factory.py`.

| Task | Status | Notes |
|---|---|---|
| Architecture inspection first (as explicitly required) | DONE | Read `capture/protocols.py`, `capture/macos/{backend,helper_client}.py`, `infrastructure/subprocess_manager.py`, all 950 lines of `native/macos/Sources/main.swift`, `native/macos/PROTOCOL.md`, `domain/errors.py`, `storage/paths.py`, `capture/factory.py`, and the existing Phase 7 gap analysis (session 11) before writing anything. |
| **Bug found in shared code**: `ManagedProcess._signal()` was POSIX-only | DONE | `os.killpg`/`signal.SIGKILL` don't exist on Windows (`signal.SIGKILL` isn't even defined in Python's `signal` module there) — would have raised `AttributeError` the first time a Windows helper needed force-killing. Fixed with platform-branched `_terminate`/`_kill` using `Popen.terminate()`/`Popen.kill()`, which Python already abstracts correctly. |
| **Bug found in shared code**: `resolve_helper_binary()` hardcoded a macOS-only binary name/dev path | DONE | `HELPER_BINARY_NAME` is now platform-conditional (`.exe` suffix on `win32`); dev build dir branches between `native/macos/build/` and `native/windows/build/`. |
| `HelperClient` relocated to a shared location | DONE | It had zero macOS-specific code (just drives a generic subprocess over JSON-lines) — moved from `capture/macos/helper_client.py` to `capture/helper_client.py` so `WindowsCaptureBackend` doesn't duplicate 300 lines of identical logic. Both `MacOSCaptureBackend` and `WindowsCaptureBackend` now share one implementation. |
| `capture/windows/backend.py` (`WindowsCaptureBackend`) | DONE | Structurally identical to `MacOSCaptureBackend` — same methods, same error handling, different binary name/error messages. |
| `capture/factory.py` Windows branch | DONE | `platform_name() == "win32"` returns a `WindowsCaptureBackend` factory. Also fixed a (pre-existing, unrelated-to-Windows) mypy false-positive: comparing `sys.platform` directly made every non-matching branch "unreachable" per mypy's platform narrowing on a Mac — switched to the existing `platform_name()` indirection already used elsewhere for this exact reason. |
| **API verification before writing 600+ lines blind**: tested whether this (non-Windows) machine could even compile Windows-targeted C# | DONE, with a real finding | `dotnet` 9.0.300 is installed here. Confirmed `EnableWindowsTargeting=true` lets `dotnet build`/`dotnet publish --runtime win-x64` cross-compile from macOS. **But** the product spec's suggested `Windows.Graphics.Capture` API requires the WinRT projection toolchain (`Microsoft.Windows.CsWinRT`), whose code generator (`cswinrt.exe`) is itself a native Windows executable — "cannot execute binary file" on this Mac, a hard dead end, not a missing-package error. Switched to DXGI Desktop Duplication (`Vortice.DXGI`/`Vortice.Direct3D11`) + Media Foundation (`Vortice.MediaFoundation`) + NAudio (WASAPI) — confirmed via a throwaway probe project that all three restore and compile cleanly with zero native-toolchain dependency. This is a real, user-relevant architecture deviation from the original spec, documented in `native/windows/README.md`, not a silent substitution. |
| Exact API signatures confirmed via assembly reflection, not guessed | DONE | Dumped the real public API of `Vortice.MediaFoundation`/`Vortice.DXGI`/`Vortice.Direct3D11`/`NAudio` from their restored NuGet DLLs (`IMFSinkWriter`, `MediaTypeAttributeKeys`, `IDXGIOutputDuplication.AcquireNextFrame`, `D3D11CreateDevice`, `WasapiLoopbackCapture`, etc.) before writing code against them — avoided an entire class of "plausible-looking but wrong" compile errors that guessing method signatures blind would have produced. |
| `native/windows/LocalMeetingRecorder.Capture/` (C#) | DONE, compiles, never run | `Protocol.cs` (JSON-lines I/O, mirrors `Out.shared`/command parsing from main.swift), `StartRequest.cs` (command parsing + error codes matching `native/macos/PROTOCOL.md`'s table exactly), `Permissions.cs` (unconditionally "granted" — Windows has no TCC-equivalent consent gate for these APIs, confirmed in the existing Phase 7 gap analysis before this phase started), `Displays.cs` (`Screen.AllScreens` enumeration), `Recorder.cs` (DXGI Desktop Duplication video capture, NAudio WASAPI loopback + capture audio, Media Foundation Sink Writer encode/mux, stats, stop/finalize — the bulk of the logic, ~650 lines), `Program.cs` (entry point + stdin command loop). `dotnet build` and `dotnet publish --runtime win-x64 --self-contained false` both succeed with 0 warnings/0 errors; publish was actually run and produced a real `lmr-capture-helper.exe` (PE32+ x86-64 Windows executable, confirmed via `file`) plus its managed dependency DLLs. **Nothing beyond that has been verified** — see `native/windows/README.md`'s "Status" and "Known gaps" sections for the complete, specific list (WASAPI mix-format/resample assumption, multi-monitor DPI correlation, Desktop Duplication re-acquisition edge cases, AAC encoder format support). |
| `scripts/build_helper_windows.ps1` | DONE, the `dotnet publish` command itself was tested | Mirrors `scripts/build_helper.sh`'s role. The exact `dotnet publish` invocation inside it was run directly (not just written) and confirmed to produce `native/windows/build/lmr-capture-helper.exe` successfully. |
| Tests | DONE | `tests/unit/capture/test_factory.py` (new `win32` case + the pre-existing `win32`-raises case updated, since it's no longer unsupported), `tests/unit/test_storage.py` (new platform-specific dev-build-dir-selection test). `tests/capture/test_real_capture_windows.py` (new): a Windows+`LMR_HW_TESTS=1`-gated hardware test mirroring `test_real_capture.py` exactly (same ffprobe/RMS/sync-offset assertions, `.mp4` instead of `.mov`, PowerShell `SoundPlayer` instead of `afplay`, `tasklist` instead of `pgrep`) — correctly skips on this (non-Windows) machine; **has never run for real.** 465 tests total (463 + 2 new unit tests; the gated Windows hardware test adds to the skip count, not the pass count, on this machine). `ruff format --check`/`ruff check`/`mypy` all clean. |
| Docs | DONE | `native/windows/README.md` (full design + an explicit "Status" section making the compile-vs-run distinction unmissable + a "Known gaps" list), `native/windows/PROTOCOL.md` (pointer to the one shared protocol doc, not a duplicate), `ARCHITECTURE.md`, `PLAN.md`, `TESTING.md`, this file. |

### Known limitations (Phase 8A)

- **Nothing in `native/windows/` has executed on real hardware.** This cannot be
  overstated: every "done" above means "compiles and type-checks," never "captures a real
  frame." No Windows machine was available during development. A human with real Windows
  10 (19041+)/11 hardware must run `scripts/build_helper_windows.ps1` then
  `LMR_HW_TESTS=1 pytest tests/capture/test_real_capture_windows.py -s -q` before this is
  trusted for an actual meeting.
- The WASAPI → AAC audio path assumes the capture device's mix format is close enough to
  48kHz 16-bit that a direct sample-format conversion (no resampling) is acceptable — see
  `native/windows/README.md`'s known-gaps list. Likely needs a real resampler if a given
  machine's actual mix rate differs.
- Windows packaging (PyInstaller spec, installer) was explicitly out of scope for this
  phase (capture-only, per the product spec) and was not attempted.
- `Recorder.cs`'s `VideoLoop` treats any `AcquireNextFrame` failure as fatal; Desktop
  Duplication's well-known re-acquisition scenarios (display mode change, secure-desktop
  transition, output loss) are not specifically handled, since verifying the right
  behavior for each needs real hardware to trigger them.

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
- Diarization (real `pyannote` model) and all three summary providers (real Ollama,
  OpenAI, OpenRouter) are verified only against fakes/mocks in the default suite — the
  gated real-model tests added in session 10 (`tests/speaker/`, `tests/summarization/`)
  have never actually been run in this environment (no Hugging Face token, no local
  Ollama server with a pulled model, no cloud API keys available). Do not claim
  diarization accuracy or real-provider summary quality until a human runs these with
  real credentials and reports the result.
- Session 10's diarization/summarization implementation (Phase 5C/5E) was found already
  written in the working tree, uncommitted and undocumented, at the start of that
  session — an engineering audit was run first to establish ground truth before any
  further changes were made (see the session log). Treat any future "this file hasn't
  been touched" assumption about this codebase with that precedent in mind: check `git
  status` before trusting these docs are fully current.
