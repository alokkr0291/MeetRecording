#!/usr/bin/env bash
# Build the native capture helper with the Swift command-line toolchain. No Xcode required.
#
# Output: native/macos/build/lmr-capture-helper
# The Info.plist is embedded into the binary (__TEXT,__info_plist) so macOS can attribute
# TCC permission prompts and read the usage-description strings.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC_DIR="native/macos/Sources"
OUT_DIR="native/macos/build"
OUT="$OUT_DIR/lmr-capture-helper"
PLIST="native/macos/Info.plist"
TARGET="${LMR_HELPER_TARGET:-arm64-apple-macosx15.0}"
CONFIG="${LMR_HELPER_CONFIG:-release}"   # release | debug

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Install the Command Line Tools with: xcode-select --install" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
OPT_FLAGS=(-O)
if [ "$CONFIG" = "debug" ]; then OPT_FLAGS=(-Onone -g); fi

echo "==> swiftc $(swiftc --version 2>&1 | head -1 | sed 's/.*Apple Swift version/Swift/')"
echo "==> target $TARGET ($CONFIG)"
swiftc "${OPT_FLAGS[@]}" \
  -target "$TARGET" \
  -swift-version 5 \
  -framework ScreenCaptureKit \
  -framework AVFoundation \
  -framework CoreMedia \
  -framework CoreGraphics \
  -framework Foundation \
  -framework AppKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PLIST" \
  -o "$OUT" \
  "$SRC_DIR"/*.swift

# Ad-hoc signature so the binary runs locally; Phase 6 replaces this with a Developer ID signature.
#
# --identifier is deliberately the MAIN APP's bundle ID, not a distinct helper ID: real-world
# testing of the packaged app (session 12) showed Screen Recording permission checks made from
# this helper never resolved to the main app's already-granted TCC entry when launched as a
# subprocess of the frozen "My Recorder.app" — `AVCaptureDevice.authorizationStatus` (used for
# microphone) correctly fell back to the responsible parent process, but
# `CGPreflightScreenCaptureAccess`/`SCShareableContent` (used for screen recording) did not,
# confirmed by reading the real system TCC database directly (`com.localmeetingrecorder.app`
# showed auth_value=2/granted while the app still re-prompted every launch). Signing the helper
# under the SAME identifier as the main app sidesteps that responsible-process resolution gap
# entirely: TCC looks up the identifier the helper is actually signed as, which is now the one
# already granted, rather than needing to delegate to an ancestor process at all.
codesign --force --sign - --identifier com.localmeetingrecorder.app "$OUT" >/dev/null 2>&1 || true
chmod +x "$OUT"
echo "==> built $OUT ($(du -h "$OUT" | cut -f1))"
"$OUT" --version
