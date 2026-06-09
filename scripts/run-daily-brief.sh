#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

cd "$ROOT"

# If the package is installed (pip install -e .), use the installed CLI directly.
# Otherwise fall back to PYTHONPATH for running from source without installation.
if "$PYTHON" -c "import marketanalyzeragents" 2>/dev/null; then
    exec "$PYTHON" -m marketanalyzeragents run "$@"
else
    export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
    exec "$PYTHON" -m marketanalyzeragents run "$@"
fi
