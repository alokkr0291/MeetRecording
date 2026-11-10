---
name: lmr-macos-capture
description: How the Swift ScreenCaptureKit helper works, how to build and debug it without Xcode, and TCC permission behaviour. Use when touching native/macos or capture/macos.
---

# macOS capture guidance

## Build

```bash
bash scripts/build_helper.sh        # swiftc, deployment target 15.0, embeds Info.plist
native/macos/build/lmr-capture-helper probe   # one-shot: prints permissions + version
```

No Xcode needed. Requires Command Line Tools (`xcode-select --install`).

## Why ScreenCaptureKit

- System audio on macOS is only available to apps through SCK (`SCStreamConfiguration.capturesAudio`, macOS 13+) or a virtual audio driver. SCK needs the Screen Recording permission.
- Microphone through SCK (`captureMicrophone`, macOS 15+) delivers mic samples on the same clock as video and system audio, which is why we use it instead of a separate AVAudioEngine tap.
- `SCStreamOutput` delivers `CMSampleBuffer`s for `.screen`, `.audio`, and `.microphone` types. We append them to three `AVAssetWriterInput`s.

## Timing rules inside the helper

- Call `AVAssetWriter.startSession(atSourceTime:)` once, with the PTS of the first video frame.
- Drop audio buffers whose PTS is earlier than the session start.
- Video frames may arrive with status `.idle`/`.blank` (no change); skip buffers whose attachment status is not `.complete`.
- `finishWriting` only on clean stop; on stdin EOF we still try to finish writing within 5 s so the file is playable.

## Permissions (TCC)

- Screen Recording: `CGPreflightScreenCaptureAccess()` to check, `CGRequestScreenCaptureAccess()` to prompt once. The grant is attributed to the *responsible process*: when launched from Terminal or PyCharm, that is Terminal/PyCharm, not the helper binary. The packaged `.app` gets its own entry.
- Microphone: `AVCaptureDevice.authorizationStatus(for: .audio)` / `requestAccess`. Needs `NSMicrophoneUsageDescription` in the embedded Info.plist or the process is killed on first access.
- macOS 15+ shows a monthly reconfirmation prompt for screen recording. Expected; do not attempt to suppress.
- Reset for testing: `tccutil reset ScreenCapture` and `tccutil reset Microphone` (affects all apps; user decision).

## Debugging

- `helper.log` in the session directory contains helper stderr.
- Run the helper by hand: `echo '{"cmd":"list-displays"}' | native/macos/build/lmr-capture-helper`.
- `ffprobe -show_streams recording.mov` to verify tracks; `ffmpeg -i recording.mov -map 0:a:0 -af astats -f null -` for level.

## Known pitfalls

- An unsigned helper copied to another Mac may be blocked by Gatekeeper; sign with hardened runtime in Phase 6.
- Audio sample buffers from SCK are Float32 non-interleaved stereo at 48 kHz; AVAssetWriter AAC input handles format conversion when `sourceFormatHint` is left nil and `outputSettings` specify AAC.
- SCK `minimumFrameInterval` of 1/30 s keeps CPU low; the first frame can take ~1 s.
