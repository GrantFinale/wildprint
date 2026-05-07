"""Tests for the server-side species-lock overlay.

The overlay dims every species cell at index >= ``free_count`` and stamps
a lock icon on top. The first ``free_count`` cells are left untouched so
the iconic species pop in full color.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from poster_layout.models import (
    LayoutResult,
    MasterImage,
    PlacedItem,
    PosterSpec,
    SpeciesRef,
)
from review_app.render.lock_overlay import apply_species_locks


def _ref(slug: str) -> SpeciesRef:
    return SpeciesRef(
        slug=slug,
        common_name=slug.replace("_", " ").title(),
        scientific_name=f"Genus {slug}",
        category="fish",
        relative_scale_index=1.0,
        habitat_tags=[],
    )


def _master(slug: str) -> MasterImage:
    return MasterImage(
        species_slug=slug,
        style_slug="scientific",
        image_path=Path(f"/tmp/{slug}.png"),
        width_px=200,
        height_px=200,
    )


def _placed(slug: str, x: int, y: int, w: int, h: int) -> PlacedItem:
    return PlacedItem(
        species_ref=_ref(slug),
        master=_master(slug),
        x=x,
        y=y,
        draw_width=w,
        draw_height=h,
    )


def _layout(placements: list[PlacedItem]) -> LayoutResult:
    poster = PosterSpec(
        title="Test",
        subtitle=None,
        style_slug="scientific",
        species_slugs=[p.species_ref.slug for p in placements],
        layout_style="hero",
        canvas_width=1000,
        canvas_height=1000,
    )
    return LayoutResult(poster=poster, placements=placements, warnings=[])


def _bright_canvas(w: int = 1000, h: int = 1000) -> Image.Image:
    """A bright canvas so the dim wash is easy to detect with numpy."""
    return Image.new("RGB", (w, h), (240, 240, 240))


def test_apply_locks_unlocks_first_3_species() -> None:
    """The first 3 placements remain untouched (full brightness)."""
    img = _bright_canvas()
    placements = [
        _placed("a", 50, 50, 200, 200),
        _placed("b", 300, 50, 200, 200),
        _placed("c", 550, 50, 200, 200),
        _placed("d", 50, 300, 200, 200),
        _placed("e", 300, 300, 200, 200),
    ]
    out = apply_species_locks(img, _layout(placements), free_count=3)

    arr = np.asarray(out)
    # Each unlocked cell should still match the original 240 value.
    for p in placements[:3]:
        # Sample the center of the cell to dodge any anti-aliasing on borders.
        cx = p.x + p.draw_width // 2
        cy = p.y + p.draw_height // 2
        assert arr[cy, cx, 0] >= 230, (
            f"unlocked cell at {p.species_ref.slug} got dimmed (R={arr[cy, cx, 0]})"
        )


def test_apply_locks_greys_species_4_through_n() -> None:
    """Cells at index >= free_count get visibly dimmer than the source."""
    img = _bright_canvas()
    placements = [
        _placed("a", 50, 50, 200, 200),
        _placed("b", 300, 50, 200, 200),
        _placed("c", 550, 50, 200, 200),
        _placed("d", 50, 300, 200, 200),
        _placed("e", 300, 300, 200, 200),
    ]
    out = apply_species_locks(img, _layout(placements), free_count=3)

    arr = np.asarray(out)
    # Sample cell centers — but offset by enough that the lock-icon glyph
    # doesn't dominate the reading. Use a corner of the cell instead.
    for p in placements[3:]:
        sample_x = p.x + 10
        sample_y = p.y + 10
        # 50% wash on a 240 source should land near 120. Allow generous margin.
        assert arr[sample_y, sample_x, 0] < 200, (
            f"locked cell at {p.species_ref.slug} not dimmed enough "
            f"(R={arr[sample_y, sample_x, 0]})"
        )


def test_apply_locks_paints_lock_icon_centered_in_each_locked_cell() -> None:
    """The lock icon (white pixels) appears near the center of each locked cell."""
    img = _bright_canvas()
    placements = [
        _placed("a", 50, 50, 200, 200),
        _placed("b", 300, 50, 200, 200),
        _placed("c", 550, 50, 200, 200),
        _placed("d", 50, 300, 300, 300),  # locked
    ]
    out = apply_species_locks(img, _layout(placements), free_count=3)
    arr = np.asarray(out)

    # The locked cell is at (50, 300) sized 300x300 → center ≈ (200, 450).
    # The icon is white-on-dim, so the center should be brighter than the
    # surrounding wash (i.e. brighter than the cell's corner).
    center_pixel = arr[450, 200, 0]
    corner_pixel = arr[310, 60, 0]  # well inside the dim wash
    assert center_pixel > corner_pixel, (
        f"icon not visible at locked-cell center (center={center_pixel}, corner={corner_pixel})"
    )


def test_apply_locks_handles_empty_layout() -> None:
    """Zero placements → returned image identical to input."""
    img = _bright_canvas(500, 500)
    out = apply_species_locks(img, _layout([]), free_count=3)
    assert out.size == img.size
    arr_in = np.asarray(img)
    arr_out = np.asarray(out)
    np.testing.assert_array_equal(arr_in, arr_out)


def test_apply_locks_handles_free_count_larger_than_placements() -> None:
    """If free_count >= len(placements) nothing is locked."""
    img = _bright_canvas()
    placements = [
        _placed("a", 50, 50, 200, 200),
        _placed("b", 300, 50, 200, 200),
    ]
    out = apply_species_locks(img, _layout(placements), free_count=5)
    arr_in = np.asarray(img)
    arr_out = np.asarray(out)
    np.testing.assert_array_equal(arr_in, arr_out)


def test_apply_locks_clips_cells_off_canvas() -> None:
    """Placements partly outside the canvas don't crash; they get clipped."""
    img = _bright_canvas(400, 400)
    placements = [
        _placed("a", 0, 0, 100, 100),
        _placed("b", 350, 350, 200, 200),  # extends past canvas — locked
    ]
    out = apply_species_locks(img, _layout(placements), free_count=1)
    assert out.size == img.size  # no crash, same size


def test_apply_locks_rejects_negative_free_count() -> None:
    """Negative free_count is a programmer error."""
    img = _bright_canvas(200, 200)
    with pytest.raises(ValueError):
        apply_species_locks(img, _layout([]), free_count=-1)


def test_apply_locks_preserves_input_mode() -> None:
    """RGB in → RGB out. RGBA in → RGBA out."""
    rgb = _bright_canvas(400, 400)
    placements = [_placed("a", 50, 50, 100, 100), _placed("b", 200, 50, 100, 100)]
    out_rgb = apply_species_locks(rgb, _layout(placements), free_count=1)
    assert out_rgb.mode == "RGB"

    rgba = Image.new("RGBA", (400, 400), (240, 240, 240, 255))
    out_rgba = apply_species_locks(rgba, _layout(placements), free_count=1)
    assert out_rgba.mode == "RGBA"
