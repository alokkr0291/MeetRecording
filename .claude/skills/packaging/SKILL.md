---
name: lmr-packaging
description: PyInstaller bundle layout, helper placement, Info.plist and entitlements, signing and notarization steps for LocalMeetingRecorder (Phase 6).
---

# Packaging guidance (Phase 6)

## Bundle layout

```
LocalMeetingRecorder.app/
  Contents/
    Info.plist              LSUIElement=1, NSMicrophoneUsageDescription, NSScreenCaptureUsageDescription (informational), CFBundleIdentifier com.localmeetingrecorder.app, LSMinimumSystemVersion 15.0
    MacOS/LocalMeetingRecorder
    Helpers/lmr-capture-helper      copied from native/macos/build/
    Frameworks/ (Qt, Python)
    Resources/
```

`storage/paths.py::helper_binary_path()` resolves: env override `LMR_HELPER_PATH` -> `Contents/Helpers/` when frozen -> `native/macos/build/` in dev.

## Build

```bash
bash scripts/build_helper.sh
bash scripts/build_macos.sh      # pyinstaller packaging/macos/LocalMeetingRecorder.spec
```

## Signing and notarization (requires Apple Developer Program)

1. Sign the helper first: `codesign --force --options runtime --entitlements packaging/macos/helper.entitlements --sign "Developer ID Application: ..." Contents/Helpers/lmr-capture-helper`
2. Sign the app bundle deep: `codesign --force --deep --options runtime --entitlements packaging/macos/app.entitlements --sign "Developer ID Application: ..." LocalMeetingRecorder.app`
3. `xcrun notarytool submit LocalMeetingRecorder.zip --keychain-profile ... --wait`
4. `xcrun stapler staple LocalMeetingRecorder.app`

Entitlements: `com.apple.security.device.audio-input` (microphone), `com.apple.security.cs.allow-unsigned-executable-memory` (Python/Qt JIT-less but PyInstaller often needs it), `com.apple.security.cs.disable-library-validation` (Python extension modules).

## Test outside PyCharm

Launch the `.app` from Finder. Check: tray icon appears; permission prompts name "LocalMeetingRecorder"; recording works; `pgrep lmr-capture-helper` empty after stop.

## FFmpeg

Do not bundle the Homebrew FFmpeg (GPL build). Options are listed in `LICENSES.md`.
