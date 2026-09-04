#!/usr/bin/env bash
# Start Talents and leave it running in the background.
#
#   ./scripts/start.sh          start, or report that it is already up
#   ./scripts/start.sh --stop   stop it
#
# Logs to /tmp/talents.log. The app is at http://127.0.0.1:8787/app/
set -euo pipefail

PORT=8787
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG=/tmp/talents.log

# lsof exits non-zero when nothing is listening; pipefail would abort the script.
running() { lsof -ti:"$PORT" 2>/dev/null | head -1 || true; }

if [[ "${1:-}" == "--stop" ]]; then
  pid="$(running)"
  if [[ -n "$pid" ]]; then
    kill "$pid"
    echo "Stopped Talents (pid $pid)."
  else
    echo "Talents is not running."
  fi
  exit 0
fi

pid="$(running)"
if [[ -n "$pid" ]]; then
  echo "Talents is already running (pid $pid) at http://127.0.0.1:$PORT/app/"
  exit 0
fi

cd "$ROOT/backend"
# nohup + setsid-style detach so the server outlives this shell.
nohup "$ROOT/.venv/bin/python" -m uvicorn talents.main:app \
  --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 &

for _ in $(seq 1 30); do
  sleep 1
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/app/"; then
    echo "Talents is running at http://127.0.0.1:$PORT/app/  (logs: $LOG)"
    exit 0
  fi
done

echo "Talents did not come up within 30s. Last lines of $LOG:" >&2
tail -20 "$LOG" >&2
exit 1
