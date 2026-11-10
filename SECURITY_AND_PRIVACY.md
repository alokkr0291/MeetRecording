# SECURITY_AND_PRIVACY.md — LocalMeetingRecorder

## Commitments

1. Local-first. Recordings, transcripts, and summaries are stored only under `~/Library/Application Support/LocalMeetingRecorder/` (configurable). Nothing is uploaded by default.
2. No hidden recording. Recording starts only after the user clicks Start and confirms the consent dialog. The app never starts recording at login or when a meeting application opens.
3. Visible state. While recording, the menu-bar icon changes and the elapsed timer is shown. macOS additionally shows its own screen-recording and microphone indicators; the app never attempts to hide or suppress them.
4. No permission bypass. Screen Recording and Microphone access go through the standard macOS TCC prompts. If a permission is denied, the app explains how to grant it and lets the user retry; it never attempts to work around the denial.
5. No telemetry. The app makes no network requests except, optionally and explicitly configured by the user, to a local Ollama endpoint (default `http://127.0.0.1:11434`) and to download transcription models after confirmation.
6. Minimal logging. Logs contain session IDs, state transitions, timings, error codes, and device names. Logs never contain audio samples, transcript text, summary text, or credentials.

## Data inventory

| Data | Location | Retention | Sensitivity |
|---|---|---|---|
| `recording.mov` (video + 2 audio tracks) | `recordings/<session_id>/` | until user deletes | high: third-party voices and screen content |
| `session.json` | same | same | low: timestamps, device names, status |
| `transcript.json` (Phase 4) | same | same | high |
| `summary.json` (Phase 5) | same | same | high |
| `library.sqlite` (Phase 4) | app support root | same | high (transcript FTS index) |
| `logs/*.log` | `logs/` | rotated, 10 files x 5 MB | low |
| `config.toml` | app support root | | low |

## Consent

Recording other people may require their consent depending on jurisdiction (one-party vs all-party consent laws, workplace rules, GDPR). The consent dialog requires the user to confirm that participants have been informed. The app cannot verify this; the responsibility stays with the user and this is stated in the dialog.

## Threat model (what we defend against)

| Threat | Mitigation |
|---|---|
| Accidental recording | Explicit Start + consent dialog; no auto-start |
| Recording continues after app crash | Helper exits on stdin EOF within seconds; incomplete session surfaced on next launch |
| Orphaned helper process | Python owns the process; SIGTERM then SIGKILL on stop/shutdown; test asserts no process remains |
| Prompt injection through transcript (Phase 5) | Transcript passed as delimited data; the summarizer has no tools, no shell, no file access; output is parsed as structured JSON and never executed |
| Secrets in repo | None required; `.env.example` holds only non-secret settings; `.gitignore` excludes `.env` |
| Sensitive data in logs | Logging helpers accept only metadata; code review rule in CLAUDE.md |
| Unbounded disk use | Free-space precheck before start; helper emits warning when free space drops under threshold |

## Out of scope (documented, not mitigated)

- A user with admin rights on the Mac can read the recordings. Recordings are not encrypted at rest in Milestone 1. Encryption at rest is a candidate for a later phase; macOS FileVault covers the common case.
- The app cannot detect whether the user actually informed participants.

## Native helper security

- `lmr-capture-helper` reads commands only from its stdin and writes only the output file path it was given by the parent process.
- It performs no network access and spawns no processes.
- It is built from `native/macos/Sources/main.swift` with `swiftc`; the build is reproducible from source with `scripts/build_helper.sh`.
- For distribution it must be code-signed with the hardened runtime and the `com.apple.security.device.audio-input` entitlement (see `packaging/macos/`).

## Reporting

Security issues: document in `CHANGELOG.md` under a "Security" heading once a release process exists.
