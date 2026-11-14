# CHANGELOG.md

All notable changes to LocalMeetingRecorder. Format follows Keep a Changelog; versions follow SemVer once releases begin.

## [Unreleased]

### Added
- Project scaffold, governance documents, and `.claude/` context (Phase 0).
- Python foundation: settings, structured logging, bounded subprocess manager, domain
  layer (session id, recording state machine, session metadata), session storage (Phase 1).
- Native macOS capture helper (`lmr-capture-helper`): ScreenCaptureKit + AVAssetWriter,
  JSON-lines protocol, builds via `scripts/build_helper.sh` with no Xcode (Phase 2a).
- Python ↔ Swift capture bridge: `capture/protocols.py` (typed wire models),
  `capture/macos/helper_client.py` (subprocess lifecycle, request/response over JSON
  lines, timeouts, crash detection), `capture/macos/backend.py`
  (`MacOSCaptureBackend`), `capture/fake.py` (`FakeCaptureBackend` for future
  coordinator tests) (Phase 2b).
- `media/inspector.py`: ffprobe stream/format summary and ffmpeg `astats`-based RMS
  loudness measurement, used to validate a finished recording (Phase 2c, inspector only).
- Hardware acceptance test (`tests/capture/test_real_capture.py`, gated by
  `LMR_HW_TESTS=1`) exercising real screen + system-audio + microphone capture end to end
  (Phase 2d).
- `native/macos/PROTOCOL.md`: the real, verified helper wire protocol (was referenced from
  two docs but did not exist).
- `application/recording_coordinator.py` (`RecordingCoordinator`): the recording state
  machine and session lifecycle owner — start/stop, permission + disk-space preflight,
  cooperative cancel-while-preparing, crash handling, session persistence via the
  existing `SessionStore`. Depends only on `capture.protocols.CaptureBackend`, so it works
  identically with `MacOSCaptureBackend` and `FakeCaptureBackend` (Phase 3a).
- `application/recovery.py`: resolves sessions left `in_progress` by a crash or
  force-quit into `recovered`/`unrecoverable`, without ever touching the recording file
  itself (Phase 3a).
- First usable desktop application (Phase 3b): `ui/tray.py` (menu-bar icon, Start/Stop,
  status, elapsed timer, recent recordings, Open Recordings Folder, Quit),
  `ui/consent_dialog.py` (explicit per-session consent popup), `ui/permission_guide.py`
  (Screen Recording/Microphone status and one-click System Settings links),
  `ui/coordinator_bridge.py` (runs coordinator calls off the GUI thread, re-emits results
  as Qt signals), `ui/app_controller.py` (wires it all together: check permissions, then
  consent, then start), `ui/shell.py`, `ui/icons.py`, `app.py` (composition root),
  `main.py` (entry point: settings, logging, single-instance guard, backend selection),
  `infrastructure/single_instance.py`, `capture/factory.py` (platform backend dispatch).
- `scripts/make_app_icon.py` and `scripts/build_dev_app.sh`: a minimal dev-only `.app`
  wrapper (`packaging/macos/dev/LocalMeetingRecorder.app`, Info.plist + launcher +
  generated icon) so Screen Recording/Microphone prompts have a named, iconned bundle to
  attribute to during development. Not the Phase 6 packaged build.
- Phase 6 packaging: `packaging/macos/LocalMeetingRecorder.spec` (PyInstaller onedir ->
  `.app` bundle, icon, `Info.plist` with the same usage-description keys as the dev
  wrapper), `packaging/macos/app.entitlements` / `helper.entitlements`. `scripts/build_macos.sh`
  now renders the icon, runs PyInstaller, copies `lmr-capture-helper` into
  `Contents/Helpers/`, and ad-hoc signs both the helper and the bundle (`codesign --sign -`)
  so the result passes `codesign --verify` and launches without a Developer ID. Verified:
  clean PyInstaller build, `codesign --verify --verbose=2` reports "valid on disk" and
  "satisfies its Designated Requirement", `storage/paths.py::resolve_helper_binary()`
  correctly finds the bundled helper via `bundle_contents_dir()`, and the frozen app
  correctly refuses to start a second instance via the existing single-instance guard.
  `spctl --assess` rejects it (expected — no Developer ID signature yet; first launch
  needs right-click → Open, or System Settings → Privacy & Security → Open Anyway).
  Real Gatekeeper-clean distribution still needs Developer ID signing + notarization
  (manual steps, documented in `.claude/skills/packaging/SKILL.md` — requires an Apple
  Developer Program enrollment this session cannot obtain).
- Phase 4 local transcription: `media/audio_extractor.py` (`AudioExtractor`, ffmpeg
  mixdown to 16 kHz mono WAV), `transcription/engine.py` (`TranscriptionEngine`, lazy
  `faster-whisper` model load, explicit/auto language, streamed progress),
  `transcription/models.py` (`Transcript`/`TranscriptSegment`, Pydantic, validated
  ordering), `transcription/store.py` (`TranscriptStore`, atomic `transcript.json`/
  `transcript.txt` writes), `transcription/manager.py` (`TranscriptionManager`,
  one background thread per session, duplicate-run prevention, retry). Wired in from
  `ui/app_controller.py`: a recording reaching `RecordingState.SAVED` triggers
  transcription automatically; `ui/transcription_bridge.py` re-emits manager callbacks
  as Qt signals; `ui/transcript_dialog.py` is a read-only viewer with a Retry action;
  `ui/tray.py`'s recent-recordings menu shows transcription status/progress.
  `domain/session_metadata.py` gained `TranscriptionStatus` and
  `PostProcessingArtifacts.transcript_status/_language/_model/_error` plus
  `mark_transcription_running/done/failed()` — all additive, old `session.json` files
  still load with defaults. A transcription failure never touches the session's own
  `status`, which stays `saved`. 43 new tests (unit: models/engine/store/manager with a
  mocked `WhisperModel`, no download; integration: real-ffmpeg audio extraction; UI:
  trigger/duplicate/retry wiring), plus `tests/transcription/test_real_whisper.py`
  (gated by `LMR_WHISPER_TESTS=1`) which **was** actually run this session against a
  real "tiny" model and real speech synthesized with macOS `say`.
- Phase 4.5 application settings: `settings/models.py` (`AppSettings`, typed and
  versioned), `settings/service.py` (`SettingsService`, atomic JSON persistence,
  falls back to defaults on a corrupt file). `infrastructure/credentials/`: a
  provider-neutral `CredentialStore` protocol, `MacOSKeychainStore` (via the `keyring`
  package — ctypes bindings to Security.framework, not the `security` CLI, so a secret
  never appears on a subprocess's argv), `UnavailableCredentialStore` (explicit, clear
  error on any non-macOS platform — never a silent plaintext fallback),
  `InMemoryCredentialStore` (test-only), `mask_secret()`. `transcription/providers/`:
  real, implemented clients for both OpenAI (`POST /v1/audio/transcriptions`) and
  OpenRouter (`POST /api/v1/audio/transcriptions`) — both verified via their published
  API references before implementing, not assumed — behind a shared
  `TranscriptionProviderClient` protocol that `TranscriptionEngine` (local
  faster-whisper) also satisfies, so `TranscriptionManager` never needs to know which
  provider it is driving. A cloud provider with no API key configured raises a
  distinguishable `TranscriptionError(code="missing_api_key")`, turned into an ordinary
  transcription failure — never a fatal error, never touching the recording.
  `storage/paths.py`: `StoragePaths` is no longer frozen; a configurable recordings
  directory (`set_recordings_dir_override()`, validated via
  `validate_writable_directory()`) keeps every previous location in
  `additional_recordings_dirs` so changing the setting never hides old recordings —
  `SessionStore.list_sessions()`/`session_dir()` now scan/resolve across every known
  root. `ui/settings_dialog.py`: General (recording directory + native folder picker),
  Transcription (provider dropdown showing live availability, local Whisper model size,
  masked API key with Save/Remove, Test Connection), About — every change applies
  immediately except the API key, which needs an explicit Save. `ui/tray.py` gained a
  "Settings…" menu item and a clickable tray notification that opens Settings directly
  when a transcription fails for a missing API key. 65 new tests — `InMemoryCredentialStore`
  and a fake HTTP transport throughout; never a real API key, real network call, or real
  Keychain write in the automated suite (one real Keychain set/get/delete was run
  manually this session to verify the implementation, then immediately cleaned up).
- OpenAI model selector in Settings: `whisper-1` ($0.006/min, segment timestamps — stays
  the default), `gpt-4o-mini-transcribe` ($0.003/min, cheapest, text-only),
  `gpt-4o-transcribe` ($0.006/min, most accurate, text-only); prices verified via web
  search 2026-11-10. Found and fixed a real correctness issue while implementing this:
  `gpt-4o-transcribe`/`gpt-4o-mini-transcribe` do **not** support
  `response_format=verbose_json` (only `whisper-1` does, per OpenAI's own docs) — the
  previous `HttpTranscriptionClientBase.transcribe()` would have sent an unsupported
  parameter and lost segment timestamps silently for those models. Fixed with
  `segment_capable_models` (per-model, per-client) and `_parse_transcription_response()`,
  which falls back to one segment spanning the locally-measured audio duration when the
  provider returns plain `text` with no segments/duration/language. 7 new tests
  (294 total).
- Phase 5 Parts A+B — source-separated transcription. Verified first (required by the
  spec before any implementation): the recording already captures genuinely separate
  system/microphone audio tracks (`native/macos/Sources/main.swift`'s two independent
  `AVAssetWriterInput`s), confirmed empirically against a real hardware-captured `.mov`
  via `ffprobe` (not just by reading source) — see IMPLEMENTATION_STATUS.md for the full
  finding. `media/audio_extractor.py`: `extract_track_wav16k_mono()` isolates one audio
  stream with no mixing (`-map 0:<index>`, no `amix`), alongside the existing mixdown
  method. `transcription/models.py`: `AudioSource` enum, `TranscriptSegment.source`
  (`None` = Phase 4 combined transcript, fully backward compatible), `Transcript.text`
  prefixes `[System]`/`[Mic]` per line, `Transcript.source_errors` for a partial result.
  `domain/session_metadata.py`: `TranscriptionStatus.PARTIAL` +
  `mark_transcription_partial()`. `transcription/manager.py`: when a session has both a
  system and a microphone track, extracts and transcribes each independently (same
  engine instance reused for both) and merges the segments onto one chronological,
  source-tagged timeline; falls back to the original Phase 4 single-pass mixdown when
  only one track exists (mic capture disabled, or a legacy session). One source failing
  no longer fails the whole job — it finishes `partial` rather than `failed` as long as
  at least one source succeeded; both failing still finishes `failed` exactly as before.
  Tray/transcript-viewer UI updated for the `partial` status. 19 new tests (313 total) —
  plus real end-to-end verification against an actual hardware recording with the real
  local Whisper engine, not just fakes (see IMPLEMENTATION_STATUS.md).
- Phase 5C — speaker diarization: `speaker/` (`pyannote/speaker-diarization-3.1` via
  `pyannote.audio`, lazy-loaded; Hugging Face token stored through the existing
  `CredentialStore`/Keychain). Identifies individual remote speakers within the
  system-audio track; the microphone track is always the fixed "You" identity, never
  diarized. `transcription/manager.py` gained an independent, retriable
  `diarize_async`/`_run_diarization` job that re-reads an already-written transcript and
  only updates system-segment `speaker_id`s. `ui/settings_dialog.py`'s "Speakers" tab
  (enable checkbox, HF token field, first-use download confirmation). Found already
  implemented but uncommitted and undocumented at the start of this session; see Fixed
  and Security below for what an engineering audit found wrong with it, and
  IMPLEMENTATION_STATUS.md Phase 5C for the full writeup.
- Phase 5D — processing order: diarization now runs to completion (success or failure)
  before automatic summary generation starts, when diarization is enabled, so a summary
  consumes the final, speaker-attributed transcript rather than racing diarization and
  almost always losing.
- Phase 5E — AI meeting summarization: `summarization/` (`SummaryManager`, structured
  `MeetingSummary`/`Decision`/`ActionItem` schema, append-only `summary.json`, Ollama/
  OpenAI/OpenRouter provider clients). Provider/model selection fully independent of the
  transcription provider/model; OpenAI/OpenRouter reuse the transcription provider's own
  API-key credential. `ui/settings_dialog.py`'s "AI Summary" tab. Found already
  implemented but uncommitted and undocumented at the start of this session; completes
  PLAN.md's original Phase 5 roadmap (structured schema, prompt-injection hardening,
  configurable models) — chunking/map-reduce for very long transcripts remains undone.
- Phase 5F — summary export and evidence: `summarization/markdown.py` renders the latest
  summary as `summary.md` (title, date, provider/model, executive summary, topics,
  decisions, action items, speakers, resolved timestamp references), written atomically
  alongside the existing versioned `summary.json`. `Decision.segment_refs`/
  `ActionItem.segment_refs` (additive, backward-compatible `list[int]`) let a summary
  cite which transcript segment(s) support a decision/action item, resolved against the
  new `Transcript.text_with_segment_indices` rendering sent to summary providers.
- `tests/speaker/test_real_pyannote.py`, `tests/summarization/test_real_{ollama,openai,
  openrouter}.py`: gated real-model/real-provider acceptance tests
  (`LMR_DIARIZATION_TESTS=1`, `LMR_OLLAMA_TESTS=1`, `LMR_OPENAI_SUMMARY_TESTS=1`,
  `LMR_OPENROUTER_SUMMARY_TESTS=1`), matching the existing `LMR_HW_TESTS`/
  `LMR_WHISPER_TESTS` convention. **Not run** in this environment — no Hugging Face
  token, local Ollama server, or cloud API key was available; diarization accuracy and
  real-provider summary quality remain unverified.
- Phase 6B — CPU-first, low-resource, and cross-platform readiness. A GPU dependency
  audit confirmed faster-whisper/ctranslate2 were already CPU-pinned and clean of any
  GPU library; found and fixed two real gaps instead (see Fixed below: pyannote's
  implicit-CPU reliance, and PyInstaller bundling the diarization dependency tree
  whenever it happened to be installed in the build venv). `settings/profiles.py`:
  `ProcessingProfile` (Low Resource / Balanced / Quality) — a single Settings combo box
  that sets the recommended Whisper model, caps CPU threads for both faster-whisper and
  pyannote, and enables/disables diarization, instead of exposing every individual
  knob. `infrastructure/resource_governor.py`: a new app-wide (not per-session) limiter
  so two *different* sessions' transcription/diarization/summarization jobs never run
  concurrently on Low Resource — unlimited (unchanged) on Balanced/Quality.
  `scripts/benchmark_cpu.py`: a reproducible, dependency-free CPU benchmark that
  measures each AI stage separately and never fabricates a number for a stage it
  wasn't asked to run. `scripts/install_diarization_cpu.{sh,ps1}`: install the
  `diarization` extra with CPU-only PyTorch wheels instead of PyPI's default
  CUDA-enabled build on Windows/Linux. PLAN.md's Phase 7 (Windows) section expanded
  from 3 bullets into a real gap analysis against the actual `CaptureBackend`
  protocol — no Windows capture code was written, per explicit scope.
- Phase 6C — standalone macOS DMG installer. `scripts/build_dmg.sh`: builds on
  `scripts/build_macos.sh` unchanged, stages `LocalMeetingRecorder.app` plus an
  `Applications` symlink, and runs `hdiutil create` for a standard drag-to-install
  disk image — refusing to proceed if it finds developer-only files or an actual
  private key inside the built bundle. `storage/paths.py`: `resolve_external_binary()`
  resolves `ffmpeg`/`ffprobe` against common install locations (not just the live
  `PATH`), fixing a real bug found by testing: a double-clicked `.app` gets a minimal
  LaunchServices `PATH` that excludes Homebrew, which previously broke transcription
  (not recording — capture never touches ffmpeg) for every launch-from-Finder. The
  PyInstaller exclude list grew to cover matplotlib/pandas/scipy/Pillow/scikit-learn/
  SQLAlchemy/alembic/optuna (the rest of the diarization-only tree Phase 6B's
  name-based verification missed) and the dev-only `mypy`/`pytest` tooling that was
  getting bundled via `pydantic.mypy`/`fsspec`'s own installed `conftest.py`.
- Phase 6D — rebrand and real-hardware permission fixes. The packaged app is now
  presented to the user as "My Recorder" (`.app` bundle name, `CFBundleDisplayName`,
  tray tooltip, consent-dialog text) via a new `APP_DISPLAY_NAME` constant kept
  separate from the internal `APP_NAME`/Keychain-service/Application-Support-folder
  identity, so the rebrand does not orphan any existing settings, recordings, or
  stored credentials. `RecordingCoordinator.request_permissions()` /
  `CoordinatorBridge.request_permissions_async()` actively trigger the real macOS
  consent prompt for a not-yet-determined permission instead of only ever reading
  cached status (see Fixed).
- Phase 8A — Windows native capture backend. `native/windows/LocalMeetingRecorder.Capture/`
  (C#): DXGI Desktop Duplication (screen) + NAudio WASAPI loopback/capture (system audio,
  microphone) + Media Foundation `IMFSinkWriter` (H.264/AAC encode, MP4 mux) — a deliberate
  deviation from the originally-suggested `Windows.Graphics.Capture`, whose WinRT
  projection toolchain cannot even compile on a non-Windows build machine (see
  `native/windows/README.md`). `capture/windows/backend.py` (`WindowsCaptureBackend`),
  wired into `capture/factory.py`'s `win32` branch; shares the existing, now-relocated
  `HelperClient` with macOS rather than duplicating it. `scripts/build_helper_windows.ps1`,
  `tests/capture/test_real_capture_windows.py` (gated, mirrors the macOS hardware test).
  **Compiles and type-checks (`dotnet build`/`dotnet publish` both verified); has never
  run on real Windows hardware** — no Windows machine was available during development;
  see `native/windows/README.md`'s "Status" section before relying on any of this.

### Changed
- `application/recording_coordinator.py`: added `check_permissions()`, a read-only
  passthrough to the backend with no state-machine side effects — the UI needs to show
  permission status before offering the consent dialog, which nothing previously exposed.
- `ARCHITECTURE.md`'s helper-protocol section replaced with a pointer to
  `native/macos/PROTOCOL.md`; the inline sketch had drifted from the implemented protocol
  (see Fixed).
- `capture/fake.py`: `FakeCaptureBackend.start()` now touches the output file (matching
  what the real helper's `AVAssetWriter` does), so coordinator tests can exercise the
  "verify the output file exists" check without hardware. Additive; no existing Phase 2b
  test depended on the file's absence.
- `storage/paths.py`: `StoragePaths` changed from a frozen dataclass to a plain one, so
  `recordings_dir_override` can change at runtime from the Settings window. No existing
  caller relied on frozen/hashable semantics; `recordings_dir` and `session_dir()` keep
  their old behavior exactly when no override is set.
- Added `keyring>=25.0,<26` as a core (not optional) dependency, for
  `infrastructure/credentials/macos_keychain.py`.
- Default local Whisper model changed from "small" to "base" (`config.py`,
  `settings/models.py`, `transcription/engine.py`'s `DEFAULT_MODEL_SIZE`) — a smaller
  first-download and faster default on an ordinary CPU-only computer, per Phase 6B's
  "recommended: base, tiny available for lower-end hardware" requirement.

### Fixed
- Corrected several mismatches between the originally sketched helper protocol in
  `ARCHITECTURE.md` and the real `native/macos/Sources/main.swift`: the microphone field
  is `capture_microphone`, not `capture_mic`; the protocol never carries a `session_id`;
  `stats`/`stopped` carry many more fields than the original `{elapsed_s, frames,
  audio_bytes}` sketch; `permissions` is not emitted unsolicited; `ping` and
  `request-permissions` were undocumented.
- Pinned `av<19` in the `transcription` extra: `faster-whisper` 1.2.1 calls
  `av.open(..., metadata_errors=...)`, a keyword PyAV removed in 19.0, so an unpinned
  install resolves a combination that raises `TypeError` on every real transcription.
  Found by actually running the real-model test, not hypothetically.
- `transcription/manager.py`: every UI-facing callback invocation is now guarded
  (`_safe_callback`) — a callback that raises (e.g. a Qt signal source already torn down
  because the app quit mid-job) no longer crashes the background thread, and critically,
  no longer gets mis-attributed as a transcription failure when the transcription itself
  had already succeeded. Found via a real `PytestUnhandledThreadExceptionWarning` surfaced
  by the existing `tests/unit/test_app.py` (a `FakeCaptureBackend` recording reaching
  `Saved` now triggers a real transcription attempt against its fake, empty recording
  file, whose background thread could outlive the test).
- Diarization silently reported `done` while tagging zero segments on a single-track
  (mixed-pipeline) recording — e.g. microphone capture disabled, system-audio-only.
  `TranscriptionManager._run_mixed()` left every segment's `source` as `None`, so
  `_run_diarization`'s `source is AudioSource.SYSTEM` filter always found nothing to tag.
  Fixed: a lone track with a recognized role (`system` or `microphone`) is now tagged
  with that source (and the microphone case gets the fixed `USER_SPEAKER_ID`), and
  diarization now raises a clean failure instead of reporting success when no segment
  was actually eligible. Found via the engineering audit, not hypothetically.
- Unguarded write-write race between a manual transcription retry and an in-flight
  diarization job could silently overwrite a newer transcript with speaker tags computed
  against stale segments. Fixed with a `Transcript.revision` counter and a per-session
  lock (`TranscriptionManager._session_lock`): diarization now re-checks the revision
  immediately before its write and fails cleanly ("the transcript changed while
  diarization was running") instead of clobbering a concurrent retry's result.
  Reproduced deterministically with a blocking fake diarization engine, not just
  reasoned about.
- Ollama's "Test Connection" only checked server reachability, never whether the
  configured model was actually installed — a reachable server with a never-pulled
  model name reported "Connected successfully" and only failed later, during a real
  summary job. Fixed: it now also checks the server's installed-model list and reports
  a distinct "model not installed" message.
- A missing-API-key summary failure was silent (logged only); the equivalent
  transcription failure already showed a clickable tray notification. Fixed: summary
  failures now show the same kind of notification, and clicking either one now opens
  Settings on the specific tab relevant to the failure instead of whichever tab happened
  to be open last.
- Provider HTTP error-response bodies were embedded into exception messages (and
  therefore logs) with no length limit. Fixed with `domain/errors.py`'s new
  `sanitize_error_detail()` (whitespace-collapsed, truncated to 300 characters), applied
  in both the summarization and transcription HTTP provider clients.
- `PyannoteSpeakerDiarizationEngine` never explicitly selected a device — CPU was the
  practical outcome today only because pyannote's own fallback happens to be CPU when
  nothing else sets it, not because this code asked for it. Fixed:
  `pipeline.to(torch.device("cpu"))` is now called unconditionally after loading.
- `packaging/macos/LocalMeetingRecorder.spec` had `excludes=[]`, so PyInstaller's static
  import analysis followed `speaker/pyannote_engine.py`'s lazy `torch`/`pyannote.audio`
  imports into site-packages and would bundle the whole ~390MB dependency tree into the
  default packaged app whenever the `diarization` extra happened to be installed in the
  build environment — true for this project's own dev venv. Fixed by excluding that
  tree explicitly; verified by actually building the app both before documenting the
  fix and after (0 torch/pyannote files in the resulting `.app`, which still builds and
  signs correctly; diarization now reports "not available in this build" instead of
  crashing if ever invoked in a frozen build that lacks it).
- That same exclude list was incomplete: `matplotlib`/`pandas`/`scipy`/`Pillow`/
  `scikit-learn`/`SQLAlchemy`/`alembic`/`optuna` are transitively pulled in by
  `pyannote.metrics`/`pyannote.database`/`pyannote.pipeline`/`speechbrain` but don't
  share a name with "torch"/"pyannote", so the original grep-based verification missed
  them. Found by actually building the app and inspecting `Contents/Frameworks`, not by
  re-reading the spec; traced each package's `Required-by` via `pip show` before adding
  it to the exclude list, confirming none are needed by faster-whisper/PySide6/
  pydantic/keyring.
- `mypy` and `pytest` (declared only in this project's own `[dev]` extra) were also
  being bundled into the packaged app: `pydantic.mypy` (pydantic's optional mypy-plugin
  submodule) statically imports the real `mypy` package, and `fsspec` (a
  huggingface_hub dependency) ships a `conftest.py` that imports `pytest`/`pluggy`/
  `iniconfig` as part of its own installed package. Found in the PyInstaller `Analysis`
  warnings log; excluded.
- `scripts/build_macos.sh` ran `pip install ".[packaging]"` without `-e`, which
  silently replaced an editable dev install with a frozen copy in `site-packages` the
  moment the packaging build script was run from the main dev venv — breaking
  `storage.paths.project_root()`'s dev-mode fallback (`resolve_helper_binary()` started
  returning `None` for the dev helper path) for the rest of that shell session. Found
  by hitting it directly: `tests/capture`'s hardware test started skipping with "not
  built" despite the dev helper clearly existing on disk. Fixed by adding `-e`.
- `AudioExtractor`/`MediaInspector` passed `"ffmpeg"`/`"ffprobe"` straight through to
  subprocess calls, relying on the calling process's `PATH`. A GUI app launched by
  double-clicking it (LaunchServices) gets a minimal `PATH`
  (`/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.` on this machine) that excludes
  Homebrew's install prefix — confirmed directly (`shutil.which("ffmpeg")` returns
  `None` under that exact `PATH`), which would have silently broken transcription
  (recording itself never touches ffmpeg) for every launch from Finder. Fixed with
  `storage/paths.py::resolve_external_binary()`: checks the live `PATH` first, then
  common install locations (Homebrew Apple Silicon/Intel, MacPorts), and never
  touches an already-explicit path. Re-verified under a reproduced minimal `PATH`
  (`env -i`): real audio extraction and a real "tiny"-model transcription both
  completed successfully.
- `check_permissions()` (used by the Start Recording flow) only ever read cached TCC
  state and never called the helper's `request_permissions` command, so a fresh
  install's first Start Recording click went straight from "not determined" to the
  app's own "open Settings" guidance — skipping the real macOS consent prompt
  entirely, leaving System Settings with no entry to toggle. Found via real hardware
  testing with a user, not hypothetically. Fixed: `app_controller.py` now calls
  `request_permissions_async()` for a not-yet-determined permission, falling back to
  the guide dialog only if it is still not granted after the real prompt.
- The Swift capture helper was ad-hoc code-signed and Info.plist-identified under its
  own distinct bundle ID (`com.localmeetingrecorder.capture-helper`), separate from the
  main app's (`com.localmeetingrecorder.app`). Confirmed by reading the real TCC
  databases directly (not the System Settings UI) that the main app's Screen Recording
  grant was genuinely present (`auth_value=2`) yet the helper's own
  `CGPreflightScreenCaptureAccess`/`SCShareableContent` calls still failed every time —
  Screen Recording's TCC enforcement did not honor responsible-process delegation from
  the helper subprocess the way `AVCaptureDevice`'s microphone check did. Fixed by
  signing/identifying the helper under the exact same bundle ID as the main app
  (`scripts/build_helper.sh`'s `codesign --identifier`, `native/macos/Info.plist`'s
  `CFBundleIdentifier`) instead of a distinct one, so no cross-process delegation is
  needed at all.
- `infrastructure/subprocess_manager.py::ManagedProcess._signal()` called
  `os.killpg(pid, signal.SIGKILL)` unconditionally — `os.killpg` doesn't exist on Windows
  and `signal.SIGKILL` isn't even defined in Python's `signal` module there, so the first
  time a Windows helper ever needed force-killing this would have raised `AttributeError`
  instead of actually killing it. Found while building the Windows capture backend
  (Phase 8A), before any Windows code tried to exercise this path. Fixed with
  platform-branched `_terminate`/`_kill` using `Popen.terminate()`/`Popen.kill()`, which
  Python already abstracts correctly cross-platform — the POSIX path (SIGTERM then
  SIGKILL to the whole process group) is unchanged.

### Security
- The transcript text handed to a summary provider (Ollama/OpenAI/OpenRouter) had no
  prompt-injection hardening despite this being an explicit requirement in PLAN.md's
  original Phase 5 roadmap — a meeting participant's spoken words went directly into
  the model's user message with no data/instruction boundary. Fixed:
  `summarization/schema.py` now wraps the transcript in `<meeting_transcript>...
  </meeting_transcript>` tags (with any literal occurrence of those tags stripped from
  the transcript text itself first, so spoken words can't forge a fake closing tag),
  and the system prompt explicitly instructs the model never to follow instructions
  found inside that boundary. No tool use or agentic capability was ever present or
  added.
