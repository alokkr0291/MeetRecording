#!/usr/bin/env bash
# Build LocalMeetingRecorder.app with PyInstaller (Phase 6 — not yet validated end to end).
# Steps: build helper -> pyinstaller spec -> copy helper into Contents/Helpers.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

bash scripts/build_helper.sh
python -m pip install -q ".[packaging]"

SPEC="packaging/macos/LocalMeetingRecorder.spec"
if [ ! -f "$SPEC" ]; then
  echo "Missing $SPEC — packaging is scheduled for Phase 6." >&2
  exit 1
fi
pyinstaller --noconfirm --clean --distpath packaging/macos/dist --workpath packaging/macos/build "$SPEC"
APP="packaging/macos/dist/LocalMeetingRecorder.app"
mkdir -p "$APP/Contents/Helpers"
cp native/macos/build/lmr-capture-helper "$APP/Contents/Helpers/"
echo "==> built $APP"
echo "Signing and notarization are documented in .claude/skills/packaging/SKILL.md"
