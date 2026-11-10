#!/usr/bin/env bash
# Formatting, lint, type check, and automated tests (unit + integration).
# Hardware tests: LMR_HW_TESTS=1 pytest tests/capture -s
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> ruff format --check"
ruff format --check src tests
echo "==> ruff check"
ruff check src tests
echo "==> mypy"
mypy
echo "==> pytest (unit + integration)"
pytest tests/unit tests/integration -q "$@"
