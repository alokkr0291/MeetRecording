# TESTING.md — LocalMeetingRecorder

## Test categories

| Category | Location | Runs in CI | Needs hardware/permissions |
|---|---|---|---|
| Unit | `tests/unit/` | yes | no |
| Integration | `tests/integration/` | yes | FFmpeg on PATH for media tests |
| Capture (hardware) | `tests/capture/` | no, gated by `LMR_HW_TESTS=1` | yes: Screen Recording + Microphone grants, an output device the mic can hear |
| Packaging | `tests/packaging/` | no, gated by `LMR_PACKAGING_TESTS=1` | built `.app` |

The suite never fakes a passing result: hardware tests `skip` (not pass) when the gate variable is absent, when the helper is not built, or when a permission is missing. Every skip prints the exact reason.

## Commands

```bash
source .venv/bin/activate
bash scripts/test.sh            # ruff format --check, ruff check, mypy, pytest (unit + integration)
pytest tests/unit -q            # unit only
LMR_HW_TESTS=1 pytest tests/capture -q -s   # real capture on this Mac
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
