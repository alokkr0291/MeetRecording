# CLAUDE.md — Agent context for LocalMeetingRecorder

Read this first in every session. Then read `IMPLEMENTATION_STATUS.md` for current progress and `.claude/project-memory.md` for decisions and environment facts.

## What this project is

A macOS menu-bar meeting recorder: PySide6 UI, Python application core, and a small Swift helper (`native/macos/`) that uses ScreenCaptureKit + AVAssetWriter to produce one `.mov` with 1 video track and 2 audio tracks (system audio, microphone). Transcription (faster-whisper) and summaries (Ollama) come in later phases.

## Ground rules

- Never claim capture works because code compiles. Only the hardware test in `tests/capture/` proves audio capture; report its actual output.
- Never delete or overwrite a user's recording. Failures in transcription/summarization must leave the recording intact.
- Never log audio, transcript text, summary text, or credentials.
- No auto-start, hidden recording, or permission workarounds.
- Subprocesses are owned: every `Popen` goes through `infrastructure/subprocess_manager.py` or `HelperClient`, has a watchdog, and is awaited on stop.
- Keep macOS specifics inside `capture/macos/` and `native/macos/`. Everything above `capture/protocols.py` must stay platform-neutral.
- Settings come from `config.py` (`LMR_` env prefix). No scattered constants.
- Tests accompany each change. Do not weaken assertions to go green; fix the cause or document the limitation.
- Update `IMPLEMENTATION_STATUS.md`, `TESTING.md` (results log), and `CHANGELOG.md` at the end of each session.

## Commands

```bash
source .venv/bin/activate
bash scripts/build_helper.sh     # builds native/macos/build/lmr-capture-helper with swiftc
bash scripts/test.sh             # ruff format --check, ruff check, mypy, pytest
bash scripts/run_dev.sh          # launches the menu-bar app
bash scripts/build_dev_app.sh    # builds packaging/macos/dev/LocalMeetingRecorder.app
                                  # (not Phase 6 packaging — just a named, iconned wrapper
                                  # so Screen Recording/Microphone prompts are attributable;
                                  # `open packaging/macos/dev/LocalMeetingRecorder.app` to run it)
LMR_HW_TESTS=1 pytest tests/capture -s -q   # real capture test (needs permissions)
LMR_WHISPER_TESTS=1 pytest tests/transcription -s -q   # real faster-whisper model (downloads on first run)
```

PyCharm: interpreter = `.venv/bin/python`; run configuration = module `local_meeting_recorder.main` with working directory = project root; mark `src` as Sources Root.

Permission attribution in dev: Homebrew's "framework" Python build unconditionally
relaunches every invocation through its own bundled `Resources/Python.app` stub (verified
this happens even for a bare `python3 -c "..."`, unrelated to this app). So even through
`build_dev_app.sh`'s wrapper, TCC may end up showing the prompt under "Python" rather
than "LocalMeetingRecorder" — same underlying access either way. The only fully reliable
fix is a frozen, signed Phase 6 build, where the running process never leaves the real
app bundle.

## Layout

```
src/local_meeting_recorder/
  main.py, app.py, config.py
  domain/          pure types: RecordingState, transitions, SessionId, SessionMetadata
  application/     RecordingCoordinator, SessionManager, recovery
  capture/         protocols.py, fake.py, macos/, windows/
  media/           inspector (ffprobe), analysis (PCM RMS/DFT), postprocess (ffmpeg),
                   audio_extractor (Phase 4)
  storage/         paths (configurable recordings dir, Phase 4.5), session_store
  infrastructure/  logging_setup, subprocess_manager, diagnostics,
                   credentials/ (Phase 4.5: CredentialStore, MacOSKeychainStore)
  settings/        (Phase 4.5) AppSettings, SettingsService (settings.json)
  ui/              tray, consent dialog, permission guide, icons,
                   transcript_dialog, transcription_bridge, settings_dialog (Phase 4.5)
  transcription/   (Phase 4) engine (faster-whisper), models, store, manager;
                   providers/ (Phase 4.5: OpenAI, OpenRouter clients + factory)
  speaker/         (Phase 5C) diarization: protocol, pyannote engine, assignment, factory
  summarization/   (Phase 5E/5F) manager, models, schema (incl. prompt-injection
                   delimiting), markdown export; providers/ (Ollama, OpenAI, OpenRouter)
native/macos/      Swift helper source, Info.plist, build output in build/
tests/             unit/, integration/, capture/ (hardware-gated), packaging/
```

## Helper protocol (summary)

JSON lines on stdin/stdout. Commands: `probe`, `list-displays`, `start`, `stop`. Events: `ready`, `permissions`, `displays`, `started`, `stats`, `warning`, `error`, `stopped`. Full schema in `ARCHITECTURE.md` and `native/macos/PROTOCOL.md`. The helper exits on stdin EOF.

## Skills

`.claude/skills/architecture`, `macos-capture`, `testing`, `packaging` contain focused guidance for those areas.
