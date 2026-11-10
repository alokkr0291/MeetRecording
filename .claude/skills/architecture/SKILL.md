---
name: lmr-architecture
description: Layer boundaries, state machine, and module placement rules for LocalMeetingRecorder. Use when adding a module, deciding where code belongs, or changing the recording lifecycle.
---

# Architecture guidance

## Placement rules

| If the code... | It belongs in |
|---|---|
| Is a pure type, enum, or validation with no I/O | `domain/` |
| Orchestrates a recording, owns state transitions | `application/` |
| Talks to an OS capture API or the native helper | `capture/<platform>/` |
| Reads/writes media files with ffprobe/ffmpeg | `media/` |
| Reads/writes files or SQLite under app support | `storage/` |
| Spawns processes, logs, reads settings | `infrastructure/` |
| Renders Qt widgets | `ui/` (no business logic) |

Dependency direction: `ui -> application -> (capture protocols, media, storage) -> infrastructure/domain`. `capture/macos` depends on `capture/protocols`, never the reverse. `application` must import only `capture.protocols`, never `capture.macos`.

## State machine

```
Idle -> Preparing -> Recording -> Finalizing -> Saved -> Idle
Preparing -> Idle        (user cancelled consent / stop during prepare)
Preparing -> Error       (permission denied, helper failed to start)
Recording -> Error       (helper crash, write error)
Finalizing -> Error      (finalize failed)
Error -> Idle            (user acknowledges)
```

`domain/recording_state.py` holds the transition table. The coordinator calls `assert_transition(current, new)` and raises `InvalidTransitionError` otherwise. Tests enumerate every pair.

## Session directory

`<app_support>/recordings/<session_id>/` with `session.json`, `recording.mov`, later `mixdown.mp4`, `audio_16k.wav`, `transcript.json`, `summary.json`, `helper.log`.

Session ID format: `lmr_YYYYMMDDTHHMMSS_<4 hex>` (local time, hex from `secrets`).

## Adding a platform

Implement `CaptureBackend` from `capture/protocols.py`, register it in `capture/factory.py` by `sys.platform`, and write tests against the same protocol test-suite used by `FakeCaptureBackend`.
