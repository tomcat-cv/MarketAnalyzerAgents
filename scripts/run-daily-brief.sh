#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

cd "$ROOT"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"

exec "$PYTHON" -m dailyresearch run "$@"
