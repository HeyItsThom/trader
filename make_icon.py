#!/usr/bin/env python3
"""
Generate a simple .icns app icon for Boston Temp Tracker.
Called automatically by build_mac.sh.

Requires Pillow (installed by build_mac.sh as a build-time dep):
    pip install Pillow
"""

import os
import subprocess
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow not found — skipping icon (app will use default PyInstaller icon).")
    sys.exit(0)

ICONSET = "assets/icon.iconset"
ICNS    = "assets/icon.icns"

# ── palette ──────────────────────────────────────────────────────────────────
BG_OUTER  = (13,   17,  23, 255)   # #0d1117
BG_INNER  = (33,   38,  45, 255)   # #21262d
ORANGE    = (240, 136,  62, 255)   # #f0883e  — observed temp line colour
RED       = (255, 123, 114, 255)   # #ff7b72  — high temp / thermometer


def draw_icon(size: int) -> Image.Image:
    px  = size
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # Outer circle (background)
    m = max(1, px // 14)
    d.ellipse([m, m, px - m, px - m], fill=BG_OUTER)

    # Inner circle (card bg)
    m2 = max(2, px * 3 // 16)
    d.ellipse([m2, m2, px - m2, px - m2], fill=BG_INNER)

    # Orange ring
    ring = max(1, px // 24)
    d.ellipse([m, m, px - m, px - m],
              outline=ORANGE, width=ring)

    # ── Thermometer ───────────────────────────────────────────────────────────
    cx      = px // 2
    sw      = max(2, px // 10)          # stem width
    s_top   = int(px * 0.20)
    s_bot   = int(px * 0.60)
    br      = max(3, px // 9)           # bulb radius
    b_cy    = int(px * 0.68)

    # Stem
    d.rounded_rectangle(
        [cx - sw // 2, s_top, cx + sw // 2, s_bot],
        radius=sw // 2,
        fill=RED,
    )

    # Bulb
    d.ellipse(
        [cx - br, b_cy - br, cx + br, b_cy + br],
        fill=RED,
    )

    # Degree symbol top-right of stem
    dot_r = max(1, px // 20)
    dot_x = cx + sw // 2 + dot_r + max(1, px // 18)
    dot_y = s_top + dot_r
    d.ellipse(
        [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
        outline=ORANGE, width=max(1, px // 36),
    )

    return img


def main():
    os.makedirs(ICONSET, exist_ok=True)

    # Required sizes for a complete .iconset
    # name format: icon_{size}x{size}.png  and  icon_{size}x{size}@2x.png
    sizes = [16, 32, 128, 256, 512]

    for base in sizes:
        for scale in (1, 2):
            px   = base * scale
            img  = draw_icon(px)
            name = f"icon_{base}x{base}.png" if scale == 1 else f"icon_{base}x{base}@2x.png"
            img.save(os.path.join(ICONSET, name))

    # iconutil is macOS-only; skip gracefully on other platforms
    if sys.platform != "darwin":
        print("Not on macOS — skipping iconutil; .icns will not be created.")
        return

    result = subprocess.run(
        ["iconutil", "-c", "icns", ICONSET, "-o", ICNS],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"✅  Icon created → {ICNS}")
    else:
        print(f"⚠️   iconutil failed: {result.stderr.strip()}")
        print("    App will use the default PyInstaller icon.")


if __name__ == "__main__":
    main()
