#!/usr/bin/env bash
# Move an existing checkout's data into the place the .app reads from.
#
#   ./scripts/migrate_to_app.sh
#
# The packaged app keeps its data in ~/Library/Application Support/Talents so that
# replacing the bundle on an update cannot destroy it. A checkout keeps its data
# in the repo. They are separate files: after this runs you have two copies, and
# whichever one you stop using will quietly go stale. Pick one and stay with it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Library/Application Support/Talents"

mkdir -p "$DEST"
copied=0

for name in talents.db .env personal_rules.json; do
  src="$ROOT/$name"
  [[ -f "$src" ]] || continue
  if [[ -f "$DEST/$name" ]]; then
    # Never overwrite: the app may already hold newer history than the checkout.
    echo "  skipped $name — already exists at the destination"
    continue
  fi
  cp "$src" "$DEST/$name"
  echo "  copied  $name"
  copied=$((copied + 1))
done

echo
if [[ $copied -eq 0 ]]; then
  echo "Nothing copied. $DEST was already set up."
else
  echo "Copied $copied file(s) to $DEST"
  echo "The app and this checkout now have separate data. Use one or the other."
fi
