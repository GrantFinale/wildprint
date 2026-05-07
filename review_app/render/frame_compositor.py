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
    return frame


__all__ = ["DEFAULT_FINISH", "frame_wrap"]
