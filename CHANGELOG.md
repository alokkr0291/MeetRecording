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

### Fixed
- Corrected several mismatches between the originally sketched helper protocol in
  `ARCHITECTURE.md` and the real `native/macos/Sources/main.swift`: the microphone field
  is `capture_microphone`, not `capture_mic`; the protocol never carries a `session_id`;
  `stats`/`stopped` carry many more fields than the original `{elapsed_s, frames,
  audio_bytes}` sketch; `permissions` is not emitted unsolicited; `ping` and
  `request-permissions` were undocumented.

### Security
