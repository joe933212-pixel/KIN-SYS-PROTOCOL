#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else "Python 3.11 or newer is required.")'
export KIN_SYS_SANDBOX_DATA_DIR="${KIN_SYS_SANDBOX_DATA_DIR:-$ROOT/sandbox-data}"
echo "Starting KIN-SYS local test sandbox..."
echo "Dashboard: http://127.0.0.1:8765"
echo "Data: $KIN_SYS_SANDBOX_DATA_DIR"
exec "$PYTHON" sandbox/portable_server.py
