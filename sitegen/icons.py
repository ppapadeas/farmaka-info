"""Draw the app icons (PNG) for installation as a PWA.

Same mark as web/static/favicon.svg: a white cross on the accent green. Run
once after changing the mark; the PNGs are committed in web/static/.

    python -m sitegen.icons
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (10, 107, 91, 255)  # #0a6b5b
WHITE = (255, 255, 255, 255)
OUT = Path(__file__).resolve().parent.parent / "web" / "static"
SCALE = 4  # draw large, then downsample for smooth edges


def _cross(draw: ImageDraw.ImageDraw, size: int, extent: float) -> None:
    """Cross centred in the canvas; `extent` is its width as a fraction of `size`."""
    c = size / 2
    arm = size * extent / 2  # half length
    bar = size * extent / 3.6 / 2  # half thickness
    draw.rectangle([c - bar, c - arm, c + bar, c + arm], fill=WHITE)
    draw.rectangle([c - arm, c - bar, c + arm, c + bar], fill=WHITE)


def icon(size: int, maskable: bool = False) -> Image.Image:
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:
        # full-bleed background; the mark stays inside the 80% safe zone
        d.rectangle([0, 0, big, big], fill=ACCENT)
        _cross(d, big, 0.42)
    else:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=ACCENT)
        _cross(d, big, 0.56)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    icon(192).save(OUT / "icon-192.png", optimize=True)
    icon(512).save(OUT / "icon-512.png", optimize=True)
    icon(512, maskable=True).save(OUT / "icon-maskable-512.png", optimize=True)
    # iOS ignores transparency and rounds corners itself: square, full bleed
    apple = icon(180, maskable=True).convert("RGB")
    apple.save(OUT / "apple-touch-icon.png", optimize=True)
    print("icons ->", OUT)


if __name__ == "__main__":
    main()
