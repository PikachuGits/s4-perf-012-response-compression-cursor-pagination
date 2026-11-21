#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-$PWD/.venv/bin/python}"
exec "$PYTHON" -m pytest -v --cov=app --cov-report=term-missing "$@"
