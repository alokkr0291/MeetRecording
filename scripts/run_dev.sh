#!/usr/bin/env bash
# Launch the menu-bar application from source.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -x native/macos/build/lmr-capture-helper ]; then
  echo "Capture helper not built yet; building..."
  bash scripts/build_helper.sh
fi
exec python -m local_meeting_recorder.main "$@"
