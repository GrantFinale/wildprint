"""Unit tests for :mod:`poster_layout.skyline_packer`.

Pure synthetic shapes (no PIL, no disk I/O). Validates the packer's
geometric guarantees: top-down fill, center bias, collision avoidance,
body region bounds, and the no-fit return value.
"""
from __future__ import annotations

import numpy as np
import pytest

from poster_layout import skyline_packer as sp


def _rect_mask(h: int, w: int) -> np.ndarray:
    """Build an h×w solid True rect mask."""
    return np.ones((h, w), dtype=bool)


# ---- find_placement basics -------------------------------------------------


def test_find_placement_empty_canvas_returns_top_left_centered() -> None:
    """First placement on empty 100×100 canvas: top of body, x-centered."""
    canvas = np.zeros((100, 100), dtype=bool)
    mask = _rect_mask(10, 20)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=100, side_left=0, side_right=100
    )
    assert pos is not None
    y, x = pos
    assert y == 0  # top of body
    # x should be centered: target_x = 50 - 10 = 40, so origin x = 40
    assert x == 40


def test_find_placement_respects_body_top() -> None:
    canvas = np.zeros((100, 100), dtype=bool)
    mask = _rect_mask(5, 10)
    pos = sp.find_placement(
        canvas, mask, body_top=20, body_bot=100, side_left=0, side_right=100
    )
    assert pos is not None
    assert pos[0] == 20  # y can't be less than body_top


def test_find_placement_respects_body_bot() -> None:
    """Mask must fit entirely above body_bot."""
    canvas = np.full((100, 100), True, dtype=bool)  # everything occupied
    # Free a thin strip just above body_bot.
    canvas[40:50, 30:60] = False
    mask = _rect_mask(10, 20)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=50, side_left=0, side_right=100
    )
    # Body bot=50, mask h=10 → y_max = 50-10 = 40. Only y=40 works.
    assert pos == (40, 40)


def test_find_placement_respects_side_bounds() -> None:
    canvas = np.zeros((100, 100), dtype=bool)
    mask = _rect_mask(10, 20)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=100, side_left=10, side_right=50
    )
    assert pos is not None
    y, x = pos
    # x range: side_left=10, side_right=50 means mask must fit with
    # x_max = 50-20 = 30. Mask is x-centered at canvas_w/2 - mw/2 = 40,
    # but 40 > 30, so we clip to 30.
    assert 10 <= x <= 30


# ---- center bias -----------------------------------------------------------


def test_find_placement_center_bias_picks_closest_to_center() -> None:
    """Among free positions in the lowest available row, the one closest
    to the canvas center is picked."""
    canvas = np.zeros((50, 100), dtype=bool)
    # Block the center of row 0: pixels 30..70 occupied.
    canvas[0:10, 30:70] = True
    mask = _rect_mask(5, 10)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=50, side_left=0, side_right=100
    )
    # First free row: y=0 isn't fully blocked above. Wait — the mask is
    # height 5, fits in y=0 if rows 0-4 have free cells.
    # Free x ranges in row 0: [0..29] and [70..99]. Mask width 10 means
    # x_max for each range is 20 (left) or 90 (right).
    # Distance from canvas center (50) - mw/2 (5) = target x = 45:
    #   - x=20 → distance 25
    #   - x=70 → distance 25 (tie)
    # In a tie, np.argmin picks the first occurrence — x=20.
    # But the actual answer depends on the order in which free_xs_rel
    # iterates. Just assert one of the valid bottoms.
    assert pos is not None
    y, x = pos
    assert y == 0
    assert x in (20, 70)


def test_find_placement_explicit_center_x() -> None:
    """Caller can override the center_x to bias toward a different column."""
    canvas = np.zeros((50, 100), dtype=bool)
    mask = _rect_mask(5, 10)
    pos = sp.find_placement(
        canvas,
        mask,
        body_top=0,
        body_bot=50,
        side_left=0,
        side_right=100,
        center_x=80,
    )
    assert pos is not None
    y, x = pos
    # target_x = 80 - 5 = 75. Origin x = 75.
    assert x == 75


# ---- collision avoidance ---------------------------------------------------


def test_find_placement_no_overlap_with_existing() -> None:
    """Placed mask never overlaps an already-occupied region."""
    canvas = np.zeros((50, 100), dtype=bool)
    canvas[0:15, 30:60] = True  # block a region
    mask = _rect_mask(10, 20)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=50, side_left=0, side_right=100
    )
    assert pos is not None
    y, x = pos
    # Verify no overlap.
    region = canvas[y : y + 10, x : x + 20]
    assert not region.any()


def test_find_placement_no_valid_position_returns_none() -> None:
    """When the canvas is full, no placement is possible."""
    canvas = np.full((50, 100), True, dtype=bool)
    mask = _rect_mask(10, 20)
    pos = sp.find_placement(
        canvas, mask, body_top=0, body_bot=50, side_left=0, side_right=100
    )
    assert pos is None


def test_find_placement_mask_too_big_for_body() -> None:
    """Mask larger than body region returns None."""
    canvas = np.zeros((100, 100), dtype=bool)
    mask = _rect_mask(60, 50)  # bigger than body height of 40
    pos = sp.find_placement(
        canvas, mask, body_top=10, body_bot=50, side_left=0, side_right=100
    )
    assert pos is None


def test_find_placement_top_down_fill() -> None:
    """A second placement settles into the next free row, not on top of the first."""
    canvas = np.zeros((100, 100), dtype=bool)
    mask = _rect_mask(20, 100)  # full-width band
    pos1 = sp.find_placement(
        canvas, mask, body_top=0, body_bot=100, side_left=0, side_right=100
    )
    assert pos1 == (0, 0)
    sp.place_mask(canvas, mask, *pos1)
    pos2 = sp.find_placement(
        canvas, mask, body_top=0, body_bot=100, side_left=0, side_right=100
    )
    assert pos2 == (20, 0)  # right below the first band


# ---- place_mask ------------------------------------------------------------


def test_place_mask_or_semantics() -> None:
    """place_mask ORs the mask in — pre-existing pixels stay True."""
    canvas = np.zeros((10, 20), dtype=bool)
    canvas[2, 3] = True
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = True
    sp.place_mask(canvas, mask, 0, 0)
    assert canvas[0, 0]
    assert canvas[2, 3]  # pre-existing pixel preserved


# ---- pack_all (integration of find + place) --------------------------------


def test_pack_all_two_rects_top_to_bottom() -> None:
    """Two band-shaped masks pack one above the other."""
    masks = [_rect_mask(10, 100), _rect_mask(10, 100)]
    result = sp.pack_all(
        canvas_shape=(100, 100),
        masks_to_place=masks,
        body_top=0,
        body_bot=100,
        side_left=0,
        side_right=100,
    )
    assert result == [(0, 0), (10, 0)]


def test_pack_all_with_pinned_hero() -> None:
    """A pinned hero forces other fish to pack below it."""
    hero = _rect_mask(20, 60)
    others = [_rect_mask(10, 40), _rect_mask(10, 40)]
    result = sp.pack_all(
        canvas_shape=(100, 100),
        masks_to_place=others,
        body_top=0,
        body_bot=100,
        side_left=0,
        side_right=100,
        pinned=[(hero, 0, 20)],  # hero at (0, 20), occupies (0..20, 20..80)
    )
    assert result is not None
    # First non-hero must end up below or beside the hero.
    y1, x1 = result[0]
    if y1 < 20:
        # If placed in same y range as hero, must be beside it (x range
        # doesn't overlap hero's [20, 80))
        assert x1 + 40 <= 20 or x1 >= 80
    # Both placed (no None).
    assert result[0] is not None
    assert result[1] is not None


def test_pack_all_returns_none_when_one_doesnt_fit() -> None:
    """If any mask can't fit, the whole pack fails with None."""
    masks = [
        _rect_mask(60, 60),  # fits
        _rect_mask(60, 60),  # doesn't fit (canvas is 100×100)
    ]
    result = sp.pack_all(
        canvas_shape=(100, 100),
        masks_to_place=masks,
        body_top=0,
        body_bot=100,
        side_left=0,
        side_right=100,
    )
    assert result is None


def test_pack_all_empty_input() -> None:
    """No masks to place → empty list, success."""
    result = sp.pack_all(
        canvas_shape=(100, 100),
        masks_to_place=[],
        body_top=0,
        body_bot=100,
        side_left=0,
        side_right=100,
    )
    assert result == []


def test_pack_all_perf_25_fish_under_3s() -> None:
    """Sanity perf check: 25 small fish on a realistic-sized grid packs
    in well under 3 seconds. Validates the FFT approach scales."""
    import time

    canvas_shape = (675, 900)  # mask_resolution=8 production grid
    # 25 fish, slightly different sizes
    masks_to_place = [_rect_mask(40 + (i % 5) * 5, 80 + (i % 7) * 5) for i in range(25)]
    t0 = time.perf_counter()
    result = sp.pack_all(
        canvas_shape=canvas_shape,
        masks_to_place=masks_to_place,
        body_top=180,
        body_bot=860,
        side_left=40,
        side_right=860,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is not None, "25 small fish should pack on 675×900"
    # Spike measured ~85ms/attempt at this size with realistic masks.
    # Rect masks are faster than alpha masks, so 1000ms is a generous cap.
    assert elapsed_ms < 1000, f"perf regression: {elapsed_ms:.0f} ms (want <1000)"
