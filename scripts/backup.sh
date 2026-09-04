#!/usr/bin/env bash
# Back up the database, verify the copy, and keep the last 14.
#
# Uses sqlite3 .backup rather than cp, which is safe while the server holds the
# database open. A plain copy of a live SQLite file can capture a torn write.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$ROOT/talents.db"
DIR="$ROOT/backups"
KEEP=14
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DIR/talents-$STAMP.db"

[ -f "$DB" ] || { echo "no database at $DB"; exit 1; }
mkdir -p "$DIR"

sqlite3 "$DB" ".backup '$OUT'"

# A backup that has not been read back is only a hope, so check it opens and holds
# the expected number of rows.
rows_src=$(sqlite3 "$DB" "select count(*) from transactions")
rows_bak=$(sqlite3 "$OUT" "select count(*) from transactions")
integrity=$(sqlite3 "$OUT" "pragma integrity_check")

if [ "$integrity" != "ok" ] || [ "$rows_src" != "$rows_bak" ]; then
  echo "backup verification FAILED (integrity=$integrity src=$rows_src backup=$rows_bak)"
  rm -f "$OUT"
  exit 1
fi

ls -1t "$DIR"/talents-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "$(date +%Y-%m-%dT%H:%M:%S) backed up $rows_bak transactions -> $(basename "$OUT")"
