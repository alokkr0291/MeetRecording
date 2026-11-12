# TESTING.md — LocalMeetingRecorder

## Test categories

| Category | Location | Runs in CI | Needs hardware/permissions |
|---|---|---|---|
| Unit | `tests/unit/` | yes | no |
| Integration | `tests/integration/` | yes | FFmpeg on PATH for media tests |
| Capture (hardware) | `tests/capture/` | no, gated by `LMR_HW_TESTS=1` | yes: Screen Recording + Microphone grants, an output device the mic can hear |
| Transcription (real model) | `tests/transcription/` | no, gated by `LMR_WHISPER_TESTS=1` | downloads a faster-whisper model on first run |
| Diarization (real model) | `tests/speaker/` | no, gated by `LMR_DIARIZATION_TESTS=1` | a Hugging Face token that accepted pyannote's terms; downloads the model on first run |
| Summarization (real providers) | `tests/summarization/` | no, gated per provider (`LMR_OLLAMA_TESTS=1` / `LMR_OPENAI_SUMMARY_TESTS=1` / `LMR_OPENROUTER_SUMMARY_TESTS=1`) | a local Ollama server with a pulled model, or a real OpenAI/OpenRouter API key (spends credit) |
| Packaging | `tests/packaging/` | no, gated by `LMR_PACKAGING_TESTS=1` | built `.app` |

The suite never fakes a passing result: hardware tests `skip` (not pass) when the gate variable is absent, when the helper is not built, or when a permission is missing. Every skip prints the exact reason.

## Commands

```bash
source .venv/bin/activate
bash scripts/test.sh            # ruff format --check, ruff check, mypy, pytest (unit + integration)
pytest tests/unit -q            # unit only
LMR_HW_TESTS=1 pytest tests/capture -q -s   # real capture on this Mac
LMR_WHISPER_TESTS=1 pytest tests/transcription -q -s   # real faster-whisper model (downloads on first run)
LMR_DIARIZATION_TESTS=1 pytest tests/speaker -q -s   # real pyannote model (downloads on first run, needs a HF token)
LMR_OLLAMA_TESTS=1 pytest tests/summarization -q -s   # real local Ollama server (needs LMR_OLLAMA_TEST_MODEL set)

# Reproducible CPU benchmark (Phase 6B) — never part of the automated suite; prints
# real measured numbers only, and is explicit about anything it did not run:
python scripts/benchmark_cpu.py --synthesize-speech-minutes 5 --whisper-model base
```

## Hardware acceptance test (Milestone 1)

File: `tests/capture/test_real_capture.py`

Procedure (automated inside the test):
1. Generate a 440 Hz sine WAV in a temp dir with the standard library (`wave`), no external tool.
2. Start the real `MacOSCaptureBackend` (spawns `lmr-capture-helper`) recording the main display with system audio and microphone.
3. Two seconds after `started`, play the tone with `afplay` through the default output device.
4. Stop after ~8 s, wait for `stopped`, assert the helper process is gone.
5. Inspect the file with `ffprobe`: exactly 1 video stream and 2 audio streams; duration within tolerance; `nb_frames`/frame count > 0.
6. Measure per-track loudness with `ffmpeg -af astats` and dominant frequency with a NumPy-free DFT over the decoded PCM (`media/analysis.py`): system track must have RMS above the silence floor and a peak near 440 Hz; microphone track must have RMS above the silence floor (acoustic pickup from the speaker; on this machine the Jabra SPEAK 410 is both default input and output).
7. Verify no `lmr-capture-helper` process remains.

Interpretation:
- System track fails but mic track passes: ScreenCaptureKit audio not delivered (permission or OS issue). Check Screen Recording grant.
- Mic track fails but system track passes: Microphone permission, wrong input device, or speaker and mic are not acoustically coupled (headphones). Re-run with a speaker.
- Both fail: helper did not receive audio; read the helper log in the session directory.

## Failure-mode matrix (how each is tested)

| Scenario | Test type | Test |
|---|---|---|
| Duplicate start | unit | `tests/unit/application/test_recording_coordinator.py` |
| Duplicate stop | unit | same |
| Stop during Preparing | unit | same (fake backend with delayed start) |
| Helper crash while recording | unit + integration | fake backend emits crash; `tests/unit/capture/test_helper_client.py` kills fake helper process |
| Disk space below threshold | unit | coordinator precheck with injected free-space function |
| Shutdown during recording | unit | coordinator `shutdown()` awaits stop |
| Screen Recording denied | unit + manual | permission checker returns denied; manual: revoke in System Settings and start |
| Microphone denied | unit + manual | same |
| System audio unavailable | manual | helper reports `warning` event; see checklist |
| Audio device disconnected | manual | unplug USB device mid-recording |
| Incomplete session on launch | unit | `tests/unit/application/test_recovery.py` |
| A/V sync | hardware | onset offset computed in `test_real_capture.py` (informational, logged; asserted < 250 ms) |
| Finalization + playback | hardware + manual | ffprobe `moov` present; open in QuickTime |
| Audio extraction from a valid/multi-track recording | integration | `tests/integration/media/test_audio_extractor.py` (real ffmpeg-synthesized media) |
| Missing audio stream / invalid media | integration | same file |
| Transcription with mocked model | unit | `tests/unit/transcription/test_engine.py` (fake `WhisperModel`, no download/inference) |
| Timestamp validation / segment ordering | unit | `tests/unit/transcription/test_models.py` |
| Atomic transcript writes | unit | `tests/unit/transcription/test_store.py` |
| Background transcription job, duplicate prevention, retry | unit | `tests/unit/transcription/test_manager.py` (fake extractor + fake engine) |
| Transcription failure never changes recording status | unit | same file (`test_retry_after_failure_succeeds_and_recording_status_stays_saved`) |
| Real model end-to-end (actual speech via macOS `say`) | transcription (gated) | `tests/transcription/test_real_whisper.py` |
| Default settings / persistence / corrupt-file fallback | unit | `tests/unit/settings/test_service.py` |
| Configurable recordings directory, validation, history | unit | `tests/unit/test_storage.py` (new `test_set_recordings_dir_override_*`, `test_sessions_from_a_previous_recordings_dir_remain_listed_after_a_change`) |
| Credential save/get/replace/delete, masking, unavailable-platform | unit | `tests/unit/infrastructure/test_credentials.py` |
| Cloud provider clients (mocked HTTP transport) | unit | `tests/unit/transcription/test_providers.py` |
| Missing API key never a fatal error / recording untouched | unit | same file (`test_factory_raises_for_*_without_api_key`) + `tests/unit/ui/test_app_controller.py` (`test_missing_api_key_notification_click_opens_settings`) |
| Settings dialog wiring (folder picker, provider switch, key save/remove, test connection) | unit | `tests/unit/ui/test_settings_dialog.py` |
| Source-separated extraction isolates one track without mixing | integration | `tests/integration/media/test_audio_extractor.py` (real ffmpeg; `extract_track_wav16k_mono`) |
| Source-separated transcription merges onto one timeline; one source's failure yields `partial`, not `failed` | unit | `tests/unit/transcription/test_manager.py` (`test_source_separated_*`) |
| Mixed-pipeline fallback when only one audio track exists | unit | same file (`test_falls_back_to_mixed_pipeline_*`) |

## Manual validation checklist

Record result, date, macOS version, and tester for each run.

- [ ] First launch shows tray icon only, no Dock icon.
- [ ] Start Recording opens the consent dialog; Cancel returns to Idle without creating a session directory.
- [ ] With Screen Recording revoked, Start shows the permission guide with a working "Open System Settings" button; after granting, Retry succeeds.
- [ ] With Microphone revoked, same behaviour for the microphone.
- [ ] While recording: menu shows state Recording, timer increments every second, icon changes.
- [ ] Stop returns to Idle, session folder contains `recording.mov`, `session.json` has `status: saved`.
- [ ] `recording.mov` plays in QuickTime with both audio tracks selectable.
- [ ] Join a real Teams/Zoom/Meet call, record 2 minutes, confirm remote voice on track 1 and own voice on track 2.
- [ ] Quit while recording: app stops recording first, file is finalized, no `lmr-capture-helper` process remains (`pgrep lmr-capture-helper`).
- [ ] Force-kill the app (`kill -9`) while recording: helper exits within 5 s on its own; next launch offers recovery for the incomplete session.
- [ ] Fill the disk (or set `LMR_MIN_FREE_DISK_MB` very high): Start refuses with a clear message.
- [ ] 60-minute recording: file size reasonable, memory stable, timer accurate.

## Test results log

Append one entry per run.

| Date | Suite | Result | Notes |
|---|---|---|---|
| 2026-11-04 | `bash scripts/test.sh` (ruff format, ruff check, mypy, pytest unit+integration) | PASS | 102 tests (65 pre-existing + 37 new: 32 capture unit + 5 media integration) |
| 2026-11-04 | `LMR_HW_TESTS=1 pytest tests/capture -s -q` | SKIPPED | Screen Recording permission `denied` for this session's responsible process (the Claude Code CLI itself, not Terminal/PyCharm); `check_permissions()` round-tripped correctly against the real helper. Needs a human to run it from a terminal granted Screen Recording + Microphone. |
| 2026-11-10 | `bash scripts/test.sh` (ruff format, ruff check, mypy, pytest unit+integration) | PASS | 216 tests (173 pre-existing + 43 new: audio extraction, transcription models/engine/store/manager, UI wiring); repeated 4x with no flakiness |
| 2026-11-10 | `LMR_WHISPER_TESTS=1 pytest tests/transcription -s -q` | PASS | Real "tiny" faster-whisper model downloaded and run against real speech synthesized with macOS `say`; found and fixed `av<19` dependency pin (see project-memory D13) |
| 2026-11-10 | `bash scripts/test.sh` (Phase 4.5: settings, credentials, cloud providers, Settings UI) | PASS | 287 tests (216 + 71 new); repeated 3x with no flakiness; real `Application` + `SettingsDialog` construction smoke-tested end to end; one real macOS Keychain set/get/delete run manually to verify `MacOSKeychainStore`, then cleaned up |
| 2026-11-10 | `LMR_WHISPER_TESTS=1 pytest tests/transcription -s -q` | PASS | Real "tiny" faster-whisper model downloaded and run against real speech synthesized with macOS `say`; transcript correctly contained recognizable words from the spoken sentence. Found and fixed a real dependency incompatibility: `faster-whisper` 1.2.1 calls a PyAV kwarg (`metadata_errors`) that PyAV 19.x removed; pinned `av<19` in `pyproject.toml`. |
| 2026-11-10 | `LMR_HW_TESTS=1 pytest tests/capture -s -q` | PASS | Ran twice — permissions are now granted to this shell (previously blocked, see project-memory's open questions). `system_rms_db≈-9`, `mic_rms_db≈-25…-54`, both above the silence floor; `ffprobe` on the resulting `.mov` confirmed 1 video + 2 independent audio streams (Phase 5 Part A verification). |
| 2026-11-10 | `bash scripts/test.sh` (Phase 5 Parts A+B: source-separated transcription) | PASS | 313 tests (294 + 19 new); repeated 3x with no flakiness; plus a manual real end-to-end run: real hardware recording → independent per-track extraction → real local Whisper transcription of both tracks → merged, persisted `done` with no source errors |
| 2026-11-11 | `bash scripts/test.sh` (session 10: engineering audit + stabilization of pre-existing, uncommitted diarization/summarization work — single-track diarization bug, transcript write race, prompt-injection hardening, Ollama model check, missing-key notifications, Markdown export, evidence references) | PASS | 433 tests (380 + 53 new); `ruff format --check`/`ruff check`/`mypy` all clean. Real-model diarization/summarization providers remain unverified — no Hugging Face token, local Ollama server, or cloud API key available in this environment |
| 2026-11-11 | `bash scripts/test.sh` (Phase 6B: CPU-first, low-resource, cross-platform readiness) | PASS | 456 tests (433 + 23 new: `ResourceGovernor`, `settings/profiles.py`, pyannote CPU-explicit/thread-limit behavior, faster-whisper CPU-default/GPU-irrelevance, cross-session job serialization, Settings UI profile picker); `ruff format --check`/`ruff check`/`mypy` all clean |
| 2026-11-11 | `python scripts/benchmark_cpu.py --synthesize-speech-minutes 1 --whisper-model tiny` (and `base`) | REAL RESULT | Apple M2 Max, 12 logical CPUs, 32 GB RAM, macOS 26.2 arm64. 71.6s of synthesized speech: "tiny" transcribed in 9.9s wall-clock (43.9 CPU-s, 478.6 MB peak RSS); "base" in 13.8s wall-clock (62.8 CPU-s, 697.5 MB peak RSS). Both `device="cpu"`, `compute_type="int8"` |
| 2026-11-11 | `python scripts/benchmark_cpu.py --synthesize-speech-minutes 5 --whisper-model tiny` | REAL RESULT | Same hardware. 352.4s (~5.9 min) of synthesized speech transcribed in 45.0s wall-clock (214.5 CPU-s, 739.9 MB peak RSS), "tiny" model, CPU/int8 |
| 2026-11-11 | 30-minute and 60-minute transcription benchmarks; diarization and summarization benchmark stages | NOT RUN | No real 30/60-minute recording was available, and running `scripts/benchmark_cpu.py --diarize`/`--summarize-transcript` needs a Hugging Face token / a local Ollama server, neither available in this environment. Do not report these as measured — see `scripts/benchmark_cpu.py`'s `"not_run"` list for exactly what a given invocation skipped and why. |
