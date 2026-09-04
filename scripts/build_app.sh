#!/usr/bin/env bash
# Build Talents.app and wrap it in a .dmg.
#
#   ./scripts/build_app.sh
#
# Output: dist/Talents.app and dist/Talents.dmg
#
# The app is not signed with an Apple Developer certificate, so Gatekeeper will
# refuse the first launch with "damaged or incomplete". That is the quarantine
# flag, not a broken build - the README says how to clear it. Signing ad-hoc here
# at least keeps the bundle internally consistent so the flag is the only issue.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
APP="$ROOT/dist/Talents.app"
DMG="$ROOT/dist/Talents.dmg"
STAGE="$ROOT/build/dmg"

cd "$ROOT"
[[ -x "$PY" ]] || { echo "No venv at $PY — see README Setup." >&2; exit 1; }

echo "==> Building the UI"
(cd frontend && npm run build >/dev/null)
[[ -f backend/talents/static/index.html ]] || {
  echo "Frontend build produced no index.html" >&2; exit 1; }

echo "==> Building the icon"
./scripts/make_icon.sh >/dev/null

echo "==> Bundling with PyInstaller"
rm -rf "$APP" "$ROOT/build/Talents"
"$PY" -m PyInstaller --noconfirm --clean Talents.spec >/dev/null
[[ -d "$APP" ]] || { echo "PyInstaller produced no app bundle" >&2; exit 1; }

# Ad-hoc signature. Without it the bundle's own hashes disagree with its contents
# and macOS reports damage even after quarantine is cleared.
echo "==> Signing (ad-hoc)"
codesign --force --deep --sign - "$APP" 2>/dev/null || \
  echo "    codesign failed; the app still runs once quarantine is cleared."

echo "==> Building the disk image"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"     # drag-to-install target
hdiutil create -volname "Talents" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo
echo "Built:"
echo "  $APP"
echo "  $DMG  ($(du -h "$DMG" | cut -f1))"
