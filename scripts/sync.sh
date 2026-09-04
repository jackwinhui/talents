#!/usr/bin/env bash
# Nightly sync. Install with:
#   cp scripts/com.talents.sync.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.talents.sync.plist
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/sync.log"

# The running server holds the Keychain-derived encryption key, so drive the sync
# through the API rather than starting a second process that would prompt again.
if ! curl -sf -o /dev/null http://127.0.0.1:8787/health; then
  echo "$(date +%Y-%m-%dT%H:%M:%S) server not running; skipping" >> "$LOG"
  exit 0
fi

echo "$(date +%Y-%m-%dT%H:%M:%S) starting sync" >> "$LOG"

# Back up before syncing, so a bad sync is always recoverable.
"$ROOT/scripts/backup.sh" >> "$LOG" 2>&1 || echo "backup failed" >> "$LOG"

if curl -sf -X POST http://127.0.0.1:8787/api/sync >> "$LOG" 2>&1; then
  curl -sf -X POST http://127.0.0.1:8787/api/recurring/detect >> "$LOG" 2>&1 || true
else
  echo "sync failed" >> "$LOG"
  # A silent failure means stale figures, so say so where it will be seen.
  osascript -e 'display notification "Sync failed - see sync.log" with title "Talents"' \
    >/dev/null 2>&1 || true
fi

# Surface a connection that needs re-authorising; Plaid links expire periodically.
curl -sf http://127.0.0.1:8787/api/link/institutions 2>/dev/null \
  | grep -o '"last_error":"[^"]*"' | grep -v '"last_error":null' >> "$LOG" 2>&1 || true

echo "" >> "$LOG"
