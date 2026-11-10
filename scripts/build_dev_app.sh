#!/usr/bin/env bash
# Build a minimal, unsigned-for-identity-only .app wrapper so macOS attributes Screen
# Recording / Microphone permission prompts to "LocalMeetingRecorder" (with its own
# icon) instead of to Terminal, an IDE, or a bare python process — which is what you get
# running `python -m local_meeting_recorder.main` directly, and why it doesn't show up by
# name in System Settings > Privacy & Security.
#
# This is NOT the Phase 6 packaged app (see scripts/build_macos.sh, which uses
# PyInstaller, code signing, and notarization). It just execs the existing .venv in
# place from inside a real .app bundle — no freezing, no distribution. Rebuild whenever
# the icon or Info.plist changes; it is cheap.
#
# Output: packaging/macos/dev/LocalMeetingRecorder.app
# Run it: open packaging/macos/dev/LocalMeetingRecorder.app
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

APP_DIR="packaging/macos/dev/LocalMeetingRecorder.app"
CONTENTS="$APP_DIR/Contents"
rm -rf "$APP_DIR"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

if [ ! -x native/macos/build/lmr-capture-helper ]; then
  echo "Capture helper not built yet; building..."
  bash scripts/build_helper.sh
fi

echo "==> rendering AppIcon.icns"
# shellcheck disable=SC1091
source .venv/bin/activate
python3 scripts/make_app_icon.py "$CONTENTS/Resources/AppIcon.icns"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>com.localmeetingrecorder.app</string>
	<key>CFBundleName</key>
	<string>LocalMeetingRecorder</string>
	<key>CFBundleDisplayName</key>
	<string>LocalMeetingRecorder</string>
	<key>CFBundleExecutable</key>
	<string>LocalMeetingRecorder</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>0.1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>15.0</string>
	<key>LSUIElement</key>
	<true/>
	<key>NSMicrophoneUsageDescription</key>
	<string>LocalMeetingRecorder records your microphone so your own voice is part of the meeting recording. Audio never leaves this Mac.</string>
	<key>NSAudioCaptureUsageDescription</key>
	<string>LocalMeetingRecorder records system audio so the other participants' voices are part of the meeting recording. Audio never leaves this Mac.</string>
	<key>NSScreenCaptureUsageDescription</key>
	<string>LocalMeetingRecorder records the selected display so the meeting video is part of the recording. Video never leaves this Mac.</string>
</dict>
</plist>
PLIST

LAUNCHER="$CONTENTS/MacOS/LocalMeetingRecorder"
cat > "$LAUNCHER" <<SCRIPT
#!/bin/bash
exec "$PROJECT_ROOT/.venv/bin/python" -m local_meeting_recorder.main "\$@"
SCRIPT
chmod +x "$LAUNCHER"

# Ad-hoc signature so this has a stable identity across rebuilds. Best-effort: codesign
# can be fussy about a script-based CFBundleExecutable, so a failure here is not fatal —
# the bundle still works and still shows its own name/icon in System Settings either way.
codesign --force --deep --sign - --identifier com.localmeetingrecorder.app "$APP_DIR" >/dev/null 2>&1 || true

echo "==> built $APP_DIR"
echo "Run it with:  open $APP_DIR"
echo 'It will appear as "LocalMeetingRecorder" in System Settings > Privacy & Security.'
