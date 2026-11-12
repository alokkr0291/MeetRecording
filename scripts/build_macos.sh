#!/usr/bin/env bash
# Build LocalMeetingRecorder.app with PyInstaller (Phase 6 — not yet validated end to end).
# Steps: build helper -> pyinstaller spec -> copy helper into Contents/Helpers.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

bash scripts/build_helper.sh
# -e (editable): a plain `pip install ".[packaging]"` here would silently replace an
# editable dev install with a frozen, regular copy in site-packages — which breaks
# `storage.paths.project_root()`'s dev-mode fallback (it assumes `__file__` resolves
# inside the real source tree) for anyone who then runs tests/dev scripts from the same
# venv after building. Found by actually hitting this: `resolve_helper_binary()` started
# returning None for the dev build path after running this script once without -e.
python -m pip install -q -e ".[packaging]"

echo "==> rendering AppIcon.icns"
python3 scripts/make_app_icon.py packaging/macos/AppIcon.icns

SPEC="packaging/macos/LocalMeetingRecorder.spec"
if [ ! -f "$SPEC" ]; then
  echo "Missing $SPEC — packaging is scheduled for Phase 6." >&2
  exit 1
fi
pyinstaller --noconfirm --clean --distpath packaging/macos/dist --workpath packaging/macos/build "$SPEC"
APP="packaging/macos/dist/My Recorder.app"
mkdir -p "$APP/Contents/Helpers"
cp native/macos/build/lmr-capture-helper "$APP/Contents/Helpers/"

# Ad-hoc signature so the bundle has a stable identity and passes local Gatekeeper's
# baseline checks. A real "Developer ID Application" signature replaces this before
# notarization/distribution — see .claude/skills/packaging/SKILL.md.
codesign --force --deep --options runtime --entitlements packaging/macos/helper.entitlements \
  --sign - "$APP/Contents/Helpers/lmr-capture-helper"
codesign --force --deep --options runtime --entitlements packaging/macos/app.entitlements \
  --sign - "$APP"

echo "==> built $APP"
echo "Signing and notarization for distribution are documented in .claude/skills/packaging/SKILL.md"
