#!/usr/bin/env bash
# Builds a distributable LocalMeetingRecorder.dmg: Swift helper -> PyInstaller .app
# (via scripts/build_macos.sh, unchanged) -> a drag-to-Applications disk image.
#
# This produces an AD-HOC SIGNED, NOT NOTARIZED build, for local installation and
# testing on this Mac (or another Mac willing to right-click -> Open past Gatekeeper).
# It is not a Gatekeeper-clean distributable — that needs a real Developer ID and
# notarization; see .claude/skills/packaging/SKILL.md for those steps.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

bash scripts/build_macos.sh

APP_DISPLAY_NAME="My Recorder"
DMG_BASENAME="MyRecorder"  # filesystem-friendly file name; the volume/app name has the space
APP="packaging/macos/dist/$APP_DISPLAY_NAME.app"
if [ ! -d "$APP" ]; then
  echo "expected $APP to exist after scripts/build_macos.sh — build failed?" >&2
  exit 1
fi

# -- Safety nets: a DMG is a distribution artifact, not a dev workspace -----------------
# These check the real bundle contents rather than just trusting the spec's own
# `datas=[]`/`excludes=[...]` — a config file being correct on paper is not the same as
# verifying what actually ended up on disk.

echo "==> checking the bundle for developer-only files (.env, VCS metadata, project config)"
FORBIDDEN_MATCHES=$(find "$APP" \
  \( -name ".env" -o -name ".env.*" -o -iname "*secret*" -o -iname "*credential*" \
     -o -name ".git" -o -name "pyproject.toml" \) \
  2>/dev/null || true)
if [ -n "$FORBIDDEN_MATCHES" ]; then
  echo "refusing to package — found developer-only/secret-looking files inside the bundle:" >&2
  echo "$FORBIDDEN_MATCHES" >&2
  exit 1
fi
echo "    none found"

# `.pem`/`.key` matched by name alone would also flag certifi's public cacert.pem
# bundle (a normal, non-secret runtime dependency of requests/urllib3/huggingface_hub)
# — so these are checked by content instead: anything with an actual private-key
# marker is refused, a public certificate bundle is not.
echo "==> checking any .pem/.key files for an actual private key (not just the name)"
PRIVATE_KEY_MATCHES=""
while IFS= read -r -d '' candidate; do
  if grep -q "PRIVATE KEY" "$candidate" 2>/dev/null; then
    PRIVATE_KEY_MATCHES="$PRIVATE_KEY_MATCHES$candidate"$'\n'
  fi
done < <(find "$APP" \( -name "*.pem" -o -name "*.key" \) -print0 2>/dev/null)
if [ -n "$PRIVATE_KEY_MATCHES" ]; then
  echo "refusing to package — found a private key inside the bundle:" >&2
  echo "$PRIVATE_KEY_MATCHES" >&2
  exit 1
fi
echo "    none found"

echo "==> checking the bundle has no GPU/diarization dependency tree"
GPU_MATCHES=$(find "$APP" \( -iname "*torch*" -o -iname "*pyannote*" \) 2>/dev/null || true)
if [ -n "$GPU_MATCHES" ]; then
  echo "refusing to package — found torch/pyannote files despite the spec's excludes:" >&2
  echo "$GPU_MATCHES" >&2
  exit 1
fi
echo "    none found"

# -- Stage and build the DMG -------------------------------------------------------------

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
DMG_NAME="${DMG_BASENAME}-${VERSION}.dmg"
DMG_PATH="packaging/macos/dist/$DMG_NAME"

STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

echo "==> staging DMG contents"
cp -R "$APP" "$STAGING/$APP_DISPLAY_NAME.app"
ln -s /Applications "$STAGING/Applications"

rm -f "$DMG_PATH"
echo "==> creating $DMG_PATH"
hdiutil create -volname "$APP_DISPLAY_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG_PATH" -quiet

echo ""
echo "==> built $DMG_PATH"
echo "This is an AD-HOC SIGNED, NOT NOTARIZED build for local installation/testing only."
echo "On another Mac, Gatekeeper will show an 'unidentified developer' warning on first"
echo "launch (right-click -> Open, or System Settings -> Privacy & Security -> Open Anyway)."
echo "See .claude/skills/packaging/SKILL.md for real Developer ID signing + notarization."
