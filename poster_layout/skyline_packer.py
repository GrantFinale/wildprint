"""Top-down center-biased skyline packer for irregular pixel masks.

Used by :class:`FieldGuidePackedEngine` (engines_v3.py). Given a list of
binary masks and a canvas bitmap, place each mask at the position that:
1. Minimizes y (lowest possible row — top-down fill)
2. Among positions that share the minimum y, picks the x closest to the
   canvas vertical centerline (center bias, matches the v2 aesthetic)
3. Has zero overlap with already-placed masks

Why pixel-mask packing
======================

The user explicitly wanted irregular nesting — fish silhouettes that fit
together by their actual shape, not by their rectangular bounding box.
A long pike's tail tip can sit beside a tall sunfish's pectoral fin
without their bboxes overlapping. Rect packing can't do this.

Why FFT convolution
===================

Naive collision check: for each candidate (y, x), test
``(canvas[y:y+h, x:x+w] & mask).any()``. With ~1M positions and 25 fish,
this is ~10 billion ops per pack_attempt. Unusable.

Vectorized trick: ``scipy.signal.fftconvolve(canvas, mask)`` gives a 2D
array where ``result[y, x] > 0`` iff placing mask at (y, x) would overlap.
One numpy call. Position lookup is O(canvas_pixels) per fish, not O(positions * fish_pixels).

Per-attempt cost at the production size (5400×7200 / mask_res=8 → 675×900
pack grid, 25 fish) is ~85ms. Validated by spike.

Algorithm sketch
================

    PACK ATTEMPT (one fish at a time):
        ┌──────────────────────────────────────┐
        │ canvas: 2D bool grid, True = occupied│
        │ ┌────────────────────────────────┐   │
        │ │█████████████              ╱    │   │
        │ │█████████████             ▌    │   │  ← already placed:
        │ │█████████████        ▄▄▄▄█    │   │     hero + fish[0..k]
        │ │                    ▌    █    │   │
        │ │                     ▀▀▀▀     │   │
        │ │                              │   │
        │ │   ◀── valid placements ──▶   │   │
        │ │                              │   │
        │ └────────────────────────────────┘   │
        └──────────────────────────────────────┘

    fftconvolve(canvas, fish_mask) → 2D float array
    where result[y, x] > 0 means overlap at origin (y, x).

    Pick lowest-y row with at least one free (result==0) cell.
    Within that row, pick x closest to canvas_center_x.

    Mark canvas[y:y+h, x:x+w] |= fish_mask. Repeat.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve


def find_placement(
    canvas: np.ndarray,
    mask: np.ndarray,
    body_top: int,
    body_bot: int,
    side_left: int,
    side_right: int,
    center_x: int | None = None,
) -> tuple[int, int] | None:
    """Find the best (y, x) origin to place ``mask`` on ``canvas``.

    Best = lowest y where the mask doesn't overlap any True pixel on the
    canvas, tie-broken by closest x to ``center_x``. Returns None if no
    valid placement exists in the allowed body region.

    Args:
        canvas: 2D boolean array. True pixels are already-occupied.
        mask: 2D boolean array to place. Must fit inside canvas.
        body_top: Minimum y for the placement origin (inclusive).
        body_bot: Maximum y for the placement bottom edge (exclusive).
            i.e. the placed mask's bottom row must be < body_bot.
        side_left, side_right: x bounds for the placement origin
            (inclusive on left, exclusive on right edge of placed mask).
        center_x: Center column to prefer (default = canvas midline).

    Returns:
        ``(y, x)`` origin in canvas coordinates, or ``None`` if no valid
        placement found.
    """
    mh, mw = mask.shape
    H, W = canvas.shape

    if center_x is None:
        center_x = W // 2

    y_min = max(0, body_top)
    y_max = min(H - mh, body_bot - mh)
    x_min = max(0, side_left)
    x_max = min(W - mw, side_right - mw)

    if y_max < y_min or x_max < x_min:
        return None

    # Convolve canvas with mask. Result shape (H - mh + 1, W - mw + 1).
    # result[y, x] is the # of overlapping True pixels if mask origin is at (y, x).
    # We want result[y, x] == 0 cells.
    conv = fftconvolve(
        canvas.astype(np.float32),
        mask.astype(np.float32),
        mode="valid",
    )
    # Float precision tolerance: FFT can produce ~1e-7 noise on zero cells.
    free = conv < 0.5

    # Restrict to the allowed (y, x) range.
    free_region = free[y_min : y_max + 1, x_min : x_max + 1]
    if not free_region.any():
        return None

    # Lowest-y row with at least one free position.
    rows_with_free = np.where(free_region.any(axis=1))[0]
    y_best_rel = int(rows_with_free[0])

    # Within that row, x closest to (center_x - mw // 2) (so the mask is
    # x-centered on center_x).
    free_xs_rel = np.where(free_region[y_best_rel])[0]
    target_x = center_x - mw // 2
    # Convert to absolute canvas coordinates.
    x_abs_candidates = free_xs_rel + x_min
    x_best = int(x_abs_candidates[np.argmin(np.abs(x_abs_candidates - target_x))])
    y_best = y_best_rel + y_min
    return (y_best, x_best)


def place_mask(canvas: np.ndarray, mask: np.ndarray, y: int, x: int) -> None:
    """OR ``mask`` into ``canvas`` at the given origin.

    Mutates ``canvas`` in place. Used after a successful
    :func:`find_placement` call to mark the mask's footprint as occupied.
    """
    mh, mw = mask.shape
    canvas[y : y + mh, x : x + mw] |= mask


def pack_all(
    canvas_shape: tuple[int, int],
    masks_to_place: list[np.ndarray],
    body_top: int,
    body_bot: int,
    side_left: int,
    side_right: int,
    pinned: list[tuple[np.ndarray, int, int]] | None = None,
) -> list[tuple[int, int]] | None:
    """Pack a list of masks onto a canvas. Returns origins or None.

    Args:
        canvas_shape: (H, W) of the pack canvas.
        masks_to_place: Masks to place, in the order they should be tried.
        body_top, body_bot: Vertical bounds of the placement region.
        side_left, side_right: Horizontal bounds.
        pinned: Optional pre-placed masks ``(mask, y, x)`` baked into
            the canvas before packing. Hero placements use this.

    Returns:
        List of ``(y, x)`` origins parallel to ``masks_to_place`` if all
        fit. ``None`` if any mask cannot be placed.
    """
    canvas = np.zeros(canvas_shape, dtype=bool)
    if pinned:
        for pmask, py, px in pinned:
            place_mask(canvas, pmask, py, px)
    placements: list[tuple[int, int]] = []
    for m in masks_to_place:
        pos = find_placement(canvas, m, body_top, body_bot, side_left, side_right)
        if pos is None:
            return None
        placements.append(pos)
        place_mask(canvas, m, pos[0], pos[1])
    return placements


__all__ = ["find_placement", "place_mask", "pack_all"]
