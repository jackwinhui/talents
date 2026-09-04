"""Rebuild the Talents logo assets.

The hands, beams and coin stack come from `gemini-source.png`. This script adds the
two falling coins, crops the badge full-bleed (the source has a wide white margin that
wastes canvas at favicon sizes), drops the white background, and writes every export
size.

The source art is a round badge painted onto an opaque white square. Nothing about it
is transparent, so the background is removed here rather than assumed: white is flood
filled inward from the four corners, which follows the badge outline and cannot reach
the cream interior because the teal ring encloses it. A plain "make white transparent"
pass would punch holes in the artwork instead.

Run:  ../.venv/bin/python build_logo.py
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).parent
SOURCE = HERE / "gemini-source.png"
MASTER = HERE / "logo-master.png"

# Sampled from the source artwork so the added coins match its own coin stack.
GOLD = (235, 188, 82, 255)
EDGE = (140, 91, 33, 255)

# (cx, cy, rx, ry, rotation) in 2048px source coordinates. Badge centre is (1022, 1022).
FALLING_COINS = [
    (700, 574, 126, 48, 20),
    (1348, 668, 107, 43, -16),
]

BADGE_BOX = (118, 118, 1927, 1927)
LOGO_SIZES = (512, 256)
ICON_SIZES = (512, 180, 64, 32)

# Sum of per-channel distance from pure white that still counts as background.
# The badge edge is 2px wide and lands ~394 away, so there is a wide safe margin.
WHITE_TOLERANCE = 40
# Colour flood filled in to mark the background before it becomes the alpha mask.
# Any colour absent from the artwork would do; magenta is easy to spot if it leaks.
SENTINEL = (255, 0, 255)


def draw_coin(img: Image.Image, cx: int, cy: int, rx: int, ry: int,
              angle: float, width: int = 22) -> None:
    pad = width * 2 + 20
    layer = Image.new("RGBA", (rx * 2 + pad, ry * 2 + pad), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [pad // 2, pad // 2, pad // 2 + rx * 2, pad // 2 + ry * 2],
        fill=GOLD, outline=EDGE, width=width,
    )
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))


def background_alpha(rgb: Image.Image, tolerance: int = WHITE_TOLERANCE) -> Image.Image:
    """An L mask: 255 over the badge, 0 over the white surround.

    Flood filled from all four corners so only white *connected to the outside* is
    removed. The cream inside the ring is nearly as light as the background, and a
    threshold on colour alone would take it too.
    """
    probe = rgb.copy()
    width, height = probe.size
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        ImageDraw.floodfill(probe, corner, SENTINEL, thresh=tolerance)

    red, green, blue = probe.split()
    is_sentinel = ImageChops.multiply(
        ImageChops.multiply(
            red.point(lambda v: 255 if v == SENTINEL[0] else 0),
            green.point(lambda v: 255 if v == SENTINEL[1] else 0),
        ),
        blue.point(lambda v: 255 if v == SENTINEL[2] else 0),
    )
    return ImageChops.invert(is_sentinel)


def without_background(rgb: Image.Image) -> Image.Image:
    """The badge on transparency, with no white left in the pixels it bleeds into.

    Resampling reads colour from transparent pixels too, so leaving them white draws
    a pale rim around the badge at every smaller size. Spreading the edge colour
    outward first means anything that bleeds in is the ring's own teal.
    """
    alpha = background_alpha(rgb)
    # MinFilter keeps the darkest neighbour, pushing the dark ring out over the white.
    bled = rgb.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MinFilter(5))
    flat = Image.composite(rgb, bled, alpha)
    flat.putalpha(alpha)
    return flat


def main() -> None:
    base = Image.open(SOURCE).convert("RGBA")
    for coin in FALLING_COINS:
        draw_coin(base, *coin)

    master = without_background(base.convert("RGB").crop(BADGE_BOX))
    master.save(MASTER)

    for size in LOGO_SIZES:
        master.resize((size, size), Image.LANCZOS).save(HERE / f"logo-{size}.png")
    for size in ICON_SIZES:
        master.resize((size, size), Image.LANCZOS).save(HERE / f"icon-{size}.png")

    print(f"master {master.size} RGBA + {len(LOGO_SIZES) + len(ICON_SIZES)} exports")


if __name__ == "__main__":
    main()
