"""Unit tests for :class:`VintageCatalogEngine`.

The engine produces a uniform 4-column grid. These tests verify cell
uniformity, row-count math, last-row centering, and that no placement
collides with the title region.
"""
from __future__ import annotations

import math
from pathlib import Path

from poster_layout.engines import VintageCatalogEngine
from poster_layout.models import MasterImage, PosterSpec, SpeciesRef


class _StubLoader:
    def __init__(self, w: int = 1000, h: int = 500) -> None:
        self._w = w
        self._h = h

    def get(self, species_slug: str, style_slug: str) -> MasterImage:
        return MasterImage(
            species_slug=species_slug,
            style_slug=style_slug,
            image_path=Path(f"/tmp/{species_slug}.png"),
            width_px=self._w,
            height_px=self._h,
        )

    def exists(self, species_slug: str, style_slug: str) -> bool:
        return True


def _ref(slug: str) -> SpeciesRef:
    return SpeciesRef(
        slug=slug,
        common_name=slug.replace("_", " ").title(),
        scientific_name=f"Genus {slug}",
        category="fish",
        relative_scale_index=1.0,
        habitat_tags=["lake"],
    )


def _spec(canvas_w: int = 5400, canvas_h: int = 7200) -> PosterSpec:
    return PosterSpec(
        title="Test Lake",
        subtitle=None,
        style_slug="scientific",
        species_slugs=[],
        layout_style="vintage_tackle",
        canvas_width=canvas_w,
        canvas_height=canvas_h,
    )


def test_eight_species_makes_four_by_two_with_equal_cell_widths() -> None:
    """8 species in a 4-column grid -> 2 rows; all cells share the same width."""
    refs = [_ref(f"sp{i}") for i in range(8)]
    result = VintageCatalogEngine().layout(_spec(), refs, _StubLoader())
    assert len(result.placements) == 8
    # Group placements by row (within ~5% of canvas_h).
    rows: dict[int, list] = {}
    for p in result.placements:
        bucket = p.y // 200
        rows.setdefault(bucket, []).append(p)
    assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
    # Within each row, draw widths should be uniform (all cells same size,
    # masters all same aspect -> same draw width).
    for row_items in rows.values():
        widths = {p.draw_width for p in row_items}
        assert len(widths) == 1, f"non-uniform widths in row: {widths}"


def test_twelve_species_makes_four_by_three() -> None:
    refs = [_ref(f"sp{i}") for i in range(12)]
    result = VintageCatalogEngine().layout(_spec(), refs, _StubLoader())
    assert len(result.placements) == 12
    rows: dict[int, list] = {}
    for p in result.placements:
        rows.setdefault(p.y // 200, []).append(p)
    assert len(rows) == 3


def test_ten_species_last_row_centered() -> None:
    """N=10 -> 4×3 grid with 2 fish in the last row, centered within track."""
    refs = [_ref(f"sp{i}") for i in range(10)]
    spec = _spec()
    result = VintageCatalogEngine().layout(spec, refs, _StubLoader())
    assert len(result.placements) == 10
    # Last 2 placements are the partial row.
    last_row = result.placements[-2:]
    # Compute row midpoint (center of the 2 cells they occupy).
    midpoint_x = (last_row[0].x + last_row[0].draw_width // 2 +
                  last_row[1].x + last_row[1].draw_width // 2) // 2
    canvas_mid = spec.canvas_width // 2
    # Center should be within 5% of canvas-mid.
    tolerance = int(spec.canvas_width * 0.05)
    assert (
        abs(midpoint_x - canvas_mid) <= tolerance
    ), f"last row midpoint {midpoint_x} not centered (canvas mid {canvas_mid})"


def test_all_placements_below_title_band_and_above_bottom_margin() -> None:
    refs = [_ref(f"sp{i}") for i in range(8)]
    spec = _spec()
    result = VintageCatalogEngine().layout(spec, refs, _StubLoader())
    title_h = int(round(spec.canvas_height * 0.13))
    bottom_h = int(round(spec.canvas_height * 0.02))
    body_bottom = spec.canvas_height - bottom_h
    for p in result.placements:
        assert p.y >= title_h - 1, f"{p.species_ref.slug} overlaps title band"
        assert (
            p.y + p.draw_height <= body_bottom + 1
        ), f"{p.species_ref.slug} extends past bottom margin"
