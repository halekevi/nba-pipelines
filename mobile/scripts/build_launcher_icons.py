#!/usr/bin/env python3
"""Generate Android launcher PNGs from ui_runner/static/proporacle-logo-v3.png."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "ui_runner" / "static" / "proporacle-logo-v3.png"
OUT_RES = REPO_ROOT / "mobile" / "android" / "app" / "src" / "main" / "res"

# (res folder, legacy ic_launcher px, adaptive foreground layer px)
DENSITY_SIZES: list[tuple[str, int, int]] = [
    ("mipmap-mdpi", 48, 108),
    ("mipmap-hdpi", 72, 162),
    ("mipmap-xhdpi", 96, 216),
    ("mipmap-xxhdpi", 144, 324),
    ("mipmap-xxxhdpi", 192, 432),
]


def _square_icon(src_rgba: Image.Image, canvas_px: int, fit_ratio: float) -> Image.Image:
    """Center ``src_rgba`` on a transparent square; scale so it fits ``fit_ratio`` of the canvas."""
    canvas = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    w, h = src_rgba.size
    max_side = max(1, int(canvas_px * fit_ratio))
    scale = min(max_side / w, max_side / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = src_rgba.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (canvas_px - nw) // 2
    y = (canvas_px - nh) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"Missing logo source: {SRC}")
    src = Image.open(SRC).convert("RGBA")
    # ~50% keeps the wide mark inside the adaptive safe zone on most OEM masks.
    legacy_fit = 0.52
    foreground_fit = 0.50

    for folder, legacy_px, fg_px in DENSITY_SIZES:
        out_dir = OUT_RES / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        _square_icon(src, legacy_px, legacy_fit).save(out_dir / "ic_launcher.png", format="PNG")
        _square_icon(src, legacy_px, legacy_fit).save(out_dir / "ic_launcher_round.png", format="PNG")
        _square_icon(src, fg_px, foreground_fit).save(
            out_dir / "ic_launcher_foreground.png", format="PNG"
        )

    print(f"OK - wrote launcher PNGs under {OUT_RES}")


if __name__ == "__main__":
    main()
