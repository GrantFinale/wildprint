"""Diagonal repeating watermark for tier-2 previews.

Pure function — no DB, no network, no storage. Tested independently in
``tests/render/test_watermark.py``.

Spec (from integration plan, decision #11):

* Text: ``www.fishingposter.com``
* Opacity: ~6% (low enough to be unobtrusive, high enough that flattening
  the JPEG via screenshot still picks it up)
* Angle: -30 degrees (diagonal across the image)
* Tile spacing: ~600 px both axes (so a 2400 px preview gets ~16 tiles)

The watermark is defense in depth, not security — a determined scraper can
strip it. The point is to raise the bar above "kid with browser DevTools."
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_log = logging.getLogger(__name__)

# Tile spacing: distance between the top-left corners of repeating watermarks,
# measured in image pixels. 600 px is dense enough that any 1024x1024 crop
# contains the full text at least once.
_TILE_SPACING_PX: int = 600

# Font size as a fraction of the long edge. ~3% gives a watermark that's
# legible without being domineering at 2400 px (~72 px) and still readable
# scaled down to 400 px (~12 px).
_FONT_SIZE_FRACTION: float = 0.03

# Common system font paths we'll probe in order. The first one that exists
# wins; if none exist, we fall back to PIL's default bitmap font.
_FONT_CANDIDATES: tuple[str, ...] = (
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial.ttf",
    # Linux (debian/ubuntu)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Containers (alpine etc.)
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _load_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the first available TrueType font; fall back to PIL default."""
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size_px)
            except OSError as exc:  # pragma: no cover — defensive
                _log.debug("watermark font load failed for %s: %s", path, exc)
                continue
    _log.warning(
        "No TrueType font found for watermark; falling back to PIL default "
        "(text will be tiny — install dejavu-sans or arial in production)."
    )
    return ImageFont.load_default()


def apply_watermark(
    image: Image.Image,
    text: str = "www.fishingposter.com",
    opacity: float = 0.06,
    angle: int = -30,
) -> Image.Image:
    """Apply a diagonal repeating watermark to ``image``.

    Args:
        image: source image. RGB or RGBA. Not mutated; a new image is returned.
        text: watermark string. Default is ``www.fishingposter.com``.
        opacity: alpha multiplier in [0, 1]. Default 0.06 (~6%).
        angle: rotation in degrees. Default -30 (down-right diagonal).

    Returns:
        A new PIL Image in the same mode as the input. RGB inputs return RGB
        (the watermark is alpha-composited then flattened on a white backdrop
        slice — but in practice we composite directly over the source pixels
        with the alpha-mask trick so no background tint is introduced).
    """
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"opacity must be in [0, 1], got {opacity!r}")
    if not text:
        raise ValueError("watermark text must be non-empty")

    base_mode = image.mode
    long_edge = max(image.size)
    font_px = max(8, int(long_edge * _FONT_SIZE_FRACTION))
    font = _load_font(font_px)

    # ------------------------------------------------------------------
    # Build one tile: a transparent canvas with the rotated text in it.
    # We'll paste this tile at a regular grid across the full image.
    # ------------------------------------------------------------------
    # Measure the text. PIL ImageFont changed APIs across versions; getbbox
    # is the modern path and works on both FreeType + default fonts (PIL >= 10).
    tmp_canvas = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_canvas)
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    text_w = max(1, int(bbox[2] - bbox[0]))
    text_h = max(1, int(bbox[3] - bbox[1]))

    # Render the text onto its own opaque-alpha canvas, then rotate that
    # canvas. Rotating a transparent canvas with anti-aliased text preserves
    # the edges much better than rotating after pasting onto the full image.
    text_alpha = round(255 * opacity)
    text_rgba = (255, 255, 255, text_alpha)  # white text — visible on most palettes

    # Pad the text canvas so rotation doesn't clip the corners.
    pad = max(text_w, text_h)
    tile_w = text_w + pad * 2
    tile_h = text_h + pad * 2
    tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    tile_draw.text((pad, pad), text, font=font, fill=text_rgba)
    rotated = tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)

    # ------------------------------------------------------------------
    # Composite the rotated tile across the image at a regular grid.
    # ------------------------------------------------------------------
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    spacing = _TILE_SPACING_PX
    rot_w, rot_h = rotated.size

    # Start the grid one tile *before* the top-left so the diagonal wrap
    # covers the corners cleanly.
    y = -rot_h
    while y < image.size[1] + rot_h:
        x = -rot_w
        # Stagger every other row by half a spacing so the pattern looks
        # less like a checkerboard and more like a continuous wash.
        x_offset = (spacing // 2) if (y // spacing) % 2 else 0
        while x < image.size[0] + rot_w:
            overlay.alpha_composite(rotated, (x + x_offset, y))
            x += spacing
        y += spacing

    # ------------------------------------------------------------------
    # Composite onto the source image, preserve original mode.
    # ------------------------------------------------------------------
    if base_mode == "RGBA":
        result = Image.alpha_composite(image, overlay)
    else:
        # Convert source to RGBA, composite, then drop the alpha channel.
        rgba_src = image.convert("RGBA")
        composited = Image.alpha_composite(rgba_src, overlay)
        result = composited.convert("RGB")

    return result


__all__ = ["apply_watermark"]
