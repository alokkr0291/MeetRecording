#!/usr/bin/env bash
# One-time developer setup on macOS. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-/opt/homebrew/bin/python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then PYTHON=python3; fi

echo "==> Python: $($PYTHON --version) at $(command -v "$PYTHON")"
"$PYTHON" - <<'EOF'
import sys
assert sys.version_info >= (3, 12), f"Python 3.12+ required, found {sys.version}"
EOF

if [ ! -d .venv ]; then
  echo "==> Creating .venv"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
python -m pip install -e ".[dev]" -q
echo "==> Installed project + dev tools into .venv"

if command -v ffmpeg >/dev/null 2>&1; then
  echo "==> ffmpeg: $(ffmpeg -version | head -1)"
else
  echo "!!  ffmpeg not found. Install with: brew install ffmpeg  (needed for post-processing and tests)"
fi

if command -v swiftc >/dev/null 2>&1; then
  echo "==> swiftc: $(swiftc --version 2>&1 | head -1)"
  bash scripts/build_helper.sh
else
  echo "!!  swiftc not found. Install Command Line Tools: xcode-select --install"
fi

echo
echo "Done. Next steps:"
echo "  bash scripts/test.sh      # lint, types, tests"
echo "  bash scripts/run_dev.sh   # launch the menu-bar app"
echo "  PyCharm: Settings > Project > Python Interpreter > Add > Existing > $(pwd)/.venv/bin/python"
