#!/usr/bin/env bash
# Build assets/Talents.icns from the master logo.
#
# macOS wants every size in one file, and iconutil refuses the whole set if a
# single one is missing, so all ten are generated rather than relying on scaling.
#
# The badge is inset rather than filling the canvas. Apple's icon grid leaves a
# margin, and since the artwork lost its opaque white square the circle would
# otherwise sit noticeably larger than every neighbour in the Dock. Only the app
# icon is inset: the favicon and the in-app header logo are drawn inside circular
# containers, where a full-bleed badge is what you want.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
SRC="$ROOT/assets/logo-master.png"
SET="$ROOT/build/Talents.iconset"
OUT="$ROOT/assets/Talents.icns"

[[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }
[[ -x "$PY" ]] || { echo "No venv at $PY — see README Setup." >&2; exit 1; }

rm -rf "$SET"
mkdir -p "$SET"

INSET=0.88 "$PY" - "$SRC" "$SET" <<'PY'
import os
import sys
from pathlib import Path

from PIL import Image

src, out = Path(sys.argv[1]), Path(sys.argv[2])
inset = float(os.environ.get("INSET", "0.88"))
master = Image.open(src).convert("RGBA")

for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = size * scale
        canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        art = max(1, int(px * inset))
        badge = master.resize((art, art), Image.LANCZOS)
        canvas.paste(badge, ((px - art) // 2, (px - art) // 2), badge)
        suffix = "" if scale == 1 else "@2x"
        canvas.save(out / f"icon_{size}x{size}{suffix}.png")
PY

iconutil -c icns "$SET" -o "$OUT"
rm -rf "$SET"
echo "Wrote $OUT"
