"""Composite a rendered poster inside a Prodigi frame photograph.

Pure function — opens the Prodigi frame photo for the requested finish
(``classic-{finish}-blank.{png,jpg}`` under ``static/frames``), reads the
inner-print rectangle from ``data/frame_skus.json``, fits the poster
inside, and returns the composited image.

The legacy ``/create`` page renders posters bare; the new
``/api/render-framed-preview`` endpoint pipes its output through this
function so customers see a real "framed art" preview instead of a flat
JPEG. The full configurator (``/preview/<spec_hash>``) gets this for free
once the endpoint is wired in.

After compositing, the output is cropped to the wood-frame bounding box
plus a small drop-shadow buffer. Prodigi's frame photographs are square
(e.g. 2000×2000) but the actual frame only occupies a portrait region
in the middle (≈4:5), with light-grey filler around it. Cropping out the
filler lets the browser render the framed preview at usable size without
wasting screen real estate on grey bars. Bug A fix.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from PIL import Image

_log = logging.getLogger(__name__)

# Sensible default if frame_skus.json is missing or doesn't list this finish.
# Matches the value found in the actual frame_skus.json today (8/8/84/84).
_DEFAULT_INNER_RECT_PCT: dict[str, float] = {"x": 8.0, "y": 8.0, "w": 84.0, "h": 84.0}

# Default finish — matches the locked decision in the prod-ux-1 spec.
DEFAULT_FINISH: str = "brown"

_FRAMES_DIR = Path(__file__).resolve().parents[1] / "static" / "frames"
_FRAME_SKUS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "frame_skus.json"
)


class InnerRectPct(TypedDict):
    """The frame_skus.json inner_rect_pct shape."""

    x: float
    y: float
    w: float
    h: float


@lru_cache(maxsize=1)
def _load_frame_skus() -> list[dict[str, object]]:
    """Read data/frame_skus.json once and cache the parsed list."""
    try:
        with _FRAME_SKUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("frame_skus.json unreadable (%s); using defaults", exc)
        return []


def _inner_rect_for_finish(finish: str) -> InnerRectPct:
    """Return the inner-print rectangle (as percentages) for ``finish``.

    Falls back to ``_DEFAULT_INNER_RECT_PCT`` if no row in frame_skus.json
    matches this finish.
    """
    skus = _load_frame_skus()
    for row in skus:
        if not isinstance(row, dict):
            continue
        if row.get("finish_id") != finish:
            continue
        rect = row.get("inner_rect_pct")
        if isinstance(rect, dict) and {"x", "y", "w", "h"} <= rect.keys():
            try:
                return {
                    "x": float(rect["x"]),
                    "y": float(rect["y"]),
                    "w": float(rect["w"]),
                    "h": float(rect["h"]),
                }
            except (TypeError, ValueError):
                continue
    return dict(_DEFAULT_INNER_RECT_PCT)  # type: ignore[return-value]


# Drop-shadow buffer around the wood-frame bbox, expressed as a fraction of
# the frame photo's larger dimension. ~3% gives the cast shadow enough room
# to fall off naturally without adding visible grey margin.
_FRAME_BBOX_BUFFER_FRACTION: float = 0.03

# Fast cache: per-finish bbox detected from the wood pixels of the frame
# photo. Detection is "any pixel where R < 130" (wood is dark RGB ≈ 70/40/30
# for brown, even darker for black; light-grey filler is ~245). The wood
# location is stable for each finish photo so caching once at first use is
# safe.
_BBOX_CACHE: dict[str, tuple[int, int, int, int]] = {}


def _compute_frame_bbox(finish: str) -> tuple[int, int, int, int]:
    """Detect the wood-frame bounding box for ``finish``.

    Scans the frame photo for "dark" pixels (R < 130 — wood is much darker
    than the light-grey filler around it for every Prodigi finish: brown,
    black, natural, antique-silver/gold, dark-grey/light-grey, white). The
    returned bbox is a tight rectangle around all wood pixels, expanded by
    ``_FRAME_BBOX_BUFFER_FRACTION`` of the frame's larger dimension on each
    side to leave room for the drop shadow.

    Returns
    -------
    (x0, y0, x1, y1) in the frame photo's pixel coordinates, suitable for
    PIL ``Image.crop``.

    Cached per-finish — the wood pixel locations don't change between calls.

    Notes
    -----
    For ``classic-white-blank`` and other near-white frames the R<130 test
    might miss the (white) wood. As a robust fallback we also OR-in pixels
    where the channels deviate from the filler grey (R-G or R-B differs by
    >5), which catches stained/painted wood that has subtle color cast.
    """
    if finish in _BBOX_CACHE:
        return _BBOX_CACHE[finish]
    try:
        import numpy as np
    except ImportError:  # pragma: no cover — numpy is a hard dep already
        # Fall back to "no crop" if numpy is somehow unavailable.
        path = _resolve_frame_path(finish)
        with Image.open(path) as raw:
            w, h = raw.size
        return (0, 0, w, h)

    path = _resolve_frame_path(finish)
    with Image.open(path) as raw:
        rgb = np.asarray(raw.convert("RGB"))
    fh, fw, _ = rgb.shape
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # Dark (wood) pixels.
    mask = r < 130
    # Color-cast pixels — catches light-stained wood that's bright but not
    # neutral grey filler.
    cast = (np.abs(r.astype(np.int16) - g.astype(np.int16)) > 5) | (
        np.abs(r.astype(np.int16) - b.astype(np.int16)) > 5
    )
    mask = mask | cast
    if not mask.any():
        # No wood detected — fall back to the whole frame.
        bbox = (0, 0, fw, fh)
    else:
        ys, xs = np.where(mask)
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
        # Expand for drop-shadow buffer.
        buf = int(round(max(fw, fh) * _FRAME_BBOX_BUFFER_FRACTION))
        x0 = max(0, x0 - buf)
        y0 = max(0, y0 - buf)
        x1 = min(fw, x1 + buf)
        y1 = min(fh, y1 + buf)
        bbox = (x0, y0, x1, y1)
    _BBOX_CACHE[finish] = bbox
    return bbox


def _resolve_frame_path(finish: str) -> Path:
    """Find the frame photo for ``finish`` — try .jpg then .png.

    Raises FileNotFoundError if neither extension exists.
    """
    for ext in ("jpg", "png"):
        candidate = _FRAMES_DIR / f"classic-{finish}-blank.{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No frame photo found for finish={finish!r} in {_FRAMES_DIR}"
    )


def frame_wrap(
    poster_image: Image.Image,
    finish: str = DEFAULT_FINISH,
) -> Image.Image:
    """Composite ``poster_image`` inside a Prodigi frame photograph.

    Args:
        poster_image: the rendered poster (RGB or RGBA). Not mutated.
        finish: the frame finish to use (e.g. ``"brown"``, ``"black"``).
            Defaults to ``"brown"`` per the prod-ux-1 spec.

    Returns:
        A new RGB image the SAME SIZE as the frame photograph, with the
        poster scaled to fit the inner rectangle and centered inside it.
        RGB output (not RGBA) so it serializes cleanly as JPEG.

    Raises:
        FileNotFoundError: if no ``classic-{finish}-blank.{jpg,png}`` is
            present in the static/frames directory.
    """
    frame_path = _resolve_frame_path(finish)
    rect = _inner_rect_for_finish(finish)

    with Image.open(frame_path) as raw:
        frame = raw.convert("RGB").copy()

    fw, fh = frame.size

    # Compute the inner rectangle in pixel coordinates.
    inner_x = round(fw * rect["x"] / 100.0)
    inner_y = round(fh * rect["y"] / 100.0)
    inner_w = max(1, round(fw * rect["w"] / 100.0))
    inner_h = max(1, round(fh * rect["h"] / 100.0))

    # Fit the poster inside the inner rect while preserving aspect ratio.
    pw, ph = poster_image.size
    scale = min(inner_w / pw, inner_h / ph)
    new_w = max(1, round(pw * scale))
    new_h = max(1, round(ph * scale))

    poster_rgb = poster_image.convert("RGB")
    resized = poster_rgb.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

    # Center inside the inner rect.
    paste_x = inner_x + (inner_w - new_w) // 2
    paste_y = inner_y + (inner_h - new_h) // 2
    frame.paste(resized, (paste_x, paste_y))

    # Crop to the wood-frame bbox + drop-shadow buffer so the output is
    # ~4:5 portrait (the frame's natural proportion), not the photograph's
    # original square with grey filler around it. Bug A fix.
    bbox = _compute_frame_bbox(finish)
    cropped = frame.crop(bbox)
    return cropped


__all__ = ["DEFAULT_FINISH", "frame_wrap"]
