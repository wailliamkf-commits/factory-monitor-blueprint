#!/bin/zsh
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK_PYTHON="${FACTORY_MONITOR_PYTHON:-$TASK_ROOT/implementation/.venv/bin/python}"
if [[ ! -x "$TASK_PYTHON" ]]; then
  print -u2 'Python environment missing. Set FACTORY_MONITOR_PYTHON to the prepared Python 3.12 executable; see docs/12-demonstration-and-field-run.md.'
  exit 1
fi
export PYTHONPATH="$TASK_ROOT/desktop/src:$TASK_ROOT/implementation/src"
cd "$TASK_ROOT"
exec "$TASK_PYTHON" -m factory_monitor_desktop "$@"
