"""Server-side lock overlay for the FREE preview.

Pure function — given a fully-rendered poster image and the ``LayoutResult``
that produced its species placements, fade every species cell at index >=
``free_count`` to ~50% (blended toward the canvas background) and stamp a
black lock icon centered in each locked cell. The first ``free_count``
placements are left untouched so the iconic species pop in full color while
the rest tease the paid unlock — and crucially the customer can still see
the silhouette/scale/spacing of every species so the layout reads as a
complete poster, not a darkened mask.

Why server-side? The watermarked free-preview JPEG that the customer sees
(and can screenshot) needs the locks baked in — a CSS-only approach can
be inspected away. Locking at render time also means a downloaded preview
remains a faithful "this is what's behind the paywall" artifact.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from poster_layout.models import LayoutResult, PlacedItem

_log = logging.getLogger(__name__)

# Path to the bundled lock icon (white silhouette on transparency).
_LOCK_ICON_PATH = (
    Path(__file__).resolve().parents[1] / "static" / "icons" / "lock.png"
)

# Fade level for a locked cell — the cell's pixels are blended toward the
# poster background by this fraction (1.0 = unchanged, 0.5 = 50% transparent
# on the background). Picked so a customer can still see scale + silhouette
# but the species clearly reads as "behind the paywall".
_LOCK_OPACITY: float = 0.5

# Lock-icon size relative to the smaller dimension of the cell. 0.35 puts
# the icon at ~35% of the cell width — large enough to read at thumbnail
# scales, small enough to keep the species silhouette visible behind it.
_ICON_SIZE_FRACTION: float = 0.35

# Minimum / maximum pixel size for the icon — guard against pathological
# tiny or huge cells.
_ICON_MIN_PX: int = 24
_ICON_MAX_PX: int = 256


def _load_lock_icon(target_px: int) -> Image.Image:
    """Load the lock icon, resized to ``target_px`` x ``target_px``ed black.

    Returns an RGBA Image whose RGB channels are forced to black so the
    icon reads against the (now lightened) cell behind it. If the bundled
    PNG is missing we synthesize a minimal lock glyph so the renderer
    never crashes — this keeps the pipeline robust if the static asset
    gets stripped from a deploy.
    """
    target_px = max(_ICON_MIN_PX, min(_ICON_MAX_PX, target_px))
    if _LOCK_ICON_PATH.exists():
        try:
            with Image.open(_LOCK_ICON_PATH) as raw:
                icon = raw.convert("RGBA")
                if icon.size != (target_px, target_px):
                    icon = icon.resize(
                        (target_px, target_px),
                        resample=Image.Resampling.LANCZOS,
                    )
                # Force RGB to black, preserving alpha — works whether the
                # bundled PNG ships a white-on-transparent or black-on-
                # transparent silhouette.
                _, _, _, alpha = icon.split()
                black = Image.new("L", icon.size, 0)
                return Image.merge("RGBA", (black, black, black, alpha))
        except OSError as exc:  # pragma: no cover — defensive
            _log.warning("lock icon load failed (%s); using synthetic glyph", exc)
    return _synthetic_lock(target_px)


def _synthetic_lock(size_px: int) -> Image.Image:
    """Draw a simple BLACK padlock silhouette on a transparent canvas."""
    img = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Body: rounded rectangle in the bottom 60% of the canvas.
    body_top = int(size_px * 0.45)
    body_left = int(size_px * 0.20)
    body_right = int(size_px * 0.80)
    body_bottom = int(size_px * 0.92)
    body_radius = max(2, int(size_px * 0.10))
    draw.rounded_rectangle(
        [(body_left, body_top), (body_right, body_bottom)],
        radius=body_radius,
        fill=(0, 0, 0, 255),
    )
    # Shackle: a hollow arc on top — approximated with two concentric arcs.
    sh_left = int(size_px * 0.30)
    sh_right = int(size_px * 0.70)
    sh_top = int(size_px * 0.10)
    sh_bottom = int(size_px * 0.55)
    sh_thick = max(2, int(size_px * 0.10))
    draw.arc(
        [(sh_left, sh_top), (sh_right, sh_bottom)],
        start=180,
        end=360,
        fill=(0, 0, 0, 255),
        width=sh_thick,
    )
    return img


def apply_species_locks(
    image: Image.Image,
    layout: LayoutResult,
    free_count: int = 3,
) -> Image.Image:
    """Dim and lock every species cell at index >= ``free_count``.

    Args:
        image: the fully-rendered poster image. Not mutated; a new image
            is returned in the same mode as the input.
        layout: the ``LayoutResult`` whose ``placements`` produced the
            poster. Each ``PlacedItem`` carries its (x, y, draw_width,
            draw_height) cell box in the same pixel space as ``image``.
        free_count: number of species at the head of the placements list
            to leave UNLOCKED (full color, no overlay). Defaults to 3.

    Returns:
        A new PIL Image with each locked cell dimmed and the lock icon
        centered on top. Cells outside the image bounds are clipped.
    """
    if free_count < 0:
        raise ValueError(f"free_count must be >= 0, got {free_count!r}")

    placements: list[PlacedItem] = list(layout.placements or [])
    locked_items = placements[free_count:]
    if not locked_items:
        # Nothing to lock — just return a copy so the caller's contract
        # ("returns a new image") still holds.
        return image.copy()

    base_mode = image.mode
    canvas = image.convert("RGBA").copy()
    img_w, img_h = canvas.size

    # Sample the corner of the canvas as the "background" we fade toward.
    # The poster background is a solid color (rendered first), so any of
    # the four corners is a safe sample. Falls back to white if alpha=0.
    bg_sample = canvas.getpixel((0, 0))
    if isinstance(bg_sample, tuple) and len(bg_sample) >= 3 and bg_sample[3] != 0:
        bg_rgb = (bg_sample[0], bg_sample[1], bg_sample[2])
    else:
        bg_rgb = (255, 255, 255)
    fade_alpha = int(round((1.0 - _LOCK_OPACITY) * 255))
    fade_rgba = (bg_rgb[0], bg_rgb[1], bg_rgb[2], fade_alpha)

    for item in locked_items:
        x0 = max(0, int(item.x))
        y0 = max(0, int(item.y))
        x1 = min(img_w, int(item.x + item.draw_width))
        y1 = min(img_h, int(item.y + item.draw_height))
        if x1 <= x0 or y1 <= y0:
            continue  # cell entirely off-canvas — skip

        cell_w = x1 - x0
        cell_h = y1 - y0

        # Fade the cell toward the canvas background so the species reads
        # at ~50% opacity — preserving silhouette/scale/spacing without
        # darkening. Alpha-composite a translucent background-colored wash.
        wash = Image.new("RGBA", (cell_w, cell_h), fade_rgba)
        cell = canvas.crop((x0, y0, x1, y1))
        cell = Image.alpha_composite(cell, wash)
        canvas.paste(cell, (x0, y0))

        # Stamp a centered black lock icon. Size is a fraction of the
        # smaller cell dimension so wide and tall cells both look right.
        icon_target = int(min(cell_w, cell_h) * _ICON_SIZE_FRACTION)
        icon = _load_lock_icon(icon_target)
        icon_w, icon_h = icon.size
        ix = x0 + (cell_w - icon_w) // 2
        iy = y0 + (cell_h - icon_h) // 2
        canvas.alpha_composite(icon, (ix, iy))

    if base_mode == "RGBA":
        return canvas
    return canvas.convert(base_mode)


__all__ = ["apply_species_locks"]
