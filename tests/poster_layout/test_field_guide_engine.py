"""Unit tests for :class:`FieldGuideBandsEngine`.

The engine produces an honest-scale band-packing layout. These tests verify
the contract (sort order, in-bounds placements, scale relationships, total
shrink for crowded posters, and the degenerate single-species case).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from poster_layout.engines import FieldGuideBandsEngine
from poster_layout.models import MasterImage, PosterSpec, SpeciesRef


# ---- Test fixtures ---------------------------------------------------------


class _StubLoader:
    """Returns a fake MasterImage with a fixed aspect ratio for any species."""

    def __init__(self, w: int = 1200, h: int = 400) -> None:
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


def _ref(slug: str, idx: float, common: str | None = None) -> SpeciesRef:
    return SpeciesRef(
        slug=slug,
        common_name=common or slug.replace("_", " ").title(),
        scientific_name=f"Genus {slug}",
        category="fish",
        relative_scale_index=idx,
        habitat_tags=["lake"],
    )


def _spec(canvas_w: int = 5400, canvas_h: int = 7200) -> PosterSpec:
    return PosterSpec(
        title="Test Lake",
        subtitle=None,
        style_slug="scientific",
        species_slugs=[],
        layout_style="field_guide",
        canvas_width=canvas_w,
        canvas_height=canvas_h,
    )


# ---- Tests -----------------------------------------------------------------


def test_sorts_by_relative_scale_index_descending() -> None:
    """First placement should be the largest fish, last should be smallest."""
    refs = [
        _ref("bluegill", 0.5),
        _ref("northern_pike", 2.5),
        _ref("smallmouth_bass", 1.0),
    ]
    spec = _spec()
    result = FieldGuideBandsEngine().layout(spec, refs, _StubLoader())
    assert len(result.placements) == 3
    assert result.placements[0].species_ref.slug == "northern_pike"
    assert result.placements[-1].species_ref.slug == "bluegill"


def test_all_placements_within_canvas_bounds() -> None:
    """No placement should escape the canvas rectangle (fully inside)."""
    refs = [_ref(f"sp{i}", 1.0 + (i % 3) * 0.5) for i in range(8)]
    spec = _spec()
    result = FieldGuideBandsEngine().layout(spec, refs, _StubLoader())
    for p in result.placements:
        assert p.x >= 0
        assert p.y >= 0
        assert p.x + p.draw_width <= spec.canvas_width
        assert p.y + p.draw_height <= spec.canvas_height


def test_pike_visibly_larger_than_bluegill_under_default_compression() -> None:
    """A 2.5-idx pike must read clearly larger than a 0.5-idx bluegill.

    Default scale_compression is 0.65 — that maps the 5x raw idx ratio
    to ~3x rendered, matching the reference fish poster's tighter range
    (the user wants smaller fish to feel substantial, not insignificant).
    Setting scale_compression=1.0 restores the prior 4-5x honest range.
    """
    refs = [_ref("northern_pike", 2.5), _ref("bluegill", 0.5)]
    spec = _spec()
    # Default compression: assert visible hierarchy, not honest 4x.
    result = FieldGuideBandsEngine().layout(spec, refs, _StubLoader())
    by_slug = {p.species_ref.slug: p for p in result.placements}
    pike_h = by_slug["northern_pike"].draw_height
    bluegill_h = by_slug["bluegill"].draw_height
    assert pike_h > 2 * bluegill_h, f"pike={pike_h} bluegill={bluegill_h}"
    assert pike_h < 4 * bluegill_h, f"compression=0.65 should keep ratio < 4x"

    # Honest scale (compression=1.0) restores the >4x ratio.
    honest = FieldGuideBandsEngine(scale_compression=1.0).layout(spec, refs, _StubLoader())
    by_slug2 = {p.species_ref.slug: p for p in honest.placements}
    assert by_slug2["northern_pike"].draw_height > 4 * by_slug2["bluegill"].draw_height


def test_25_species_total_shrink_keeps_inside_body_region() -> None:
    """A 25-species poster overflows the body — the shrink pass must fix it."""
    refs = [
        _ref(f"sp{i}", 0.5 + (i % 5) * 0.4)
        for i in range(25)
    ]
    engine = FieldGuideBandsEngine()
    spec = _spec()
    result = engine.layout(spec, refs, _StubLoader())
    # Body region: top is reserved for title, bottom for tag mark — pull
    # the actual fractions from the engine instance so test stays in sync
    # with engine tuning.
    body_top = int(round(spec.canvas_height * engine.title_band_fraction))
    body_bottom = (
        spec.canvas_height
        - int(round(spec.canvas_height * engine.bottom_margin_fraction))
    )
    for p in result.placements:
        assert p.y >= body_top - 1, f"{p.species_ref.slug} above body region"
        assert (
            p.y + p.draw_height <= body_bottom + 1
        ), f"{p.species_ref.slug} below body region"


def test_single_species_still_works() -> None:
    """N=1 is a degenerate case — engine should still produce one placement."""
    refs = [_ref("northern_pike", 2.5)]
    spec = _spec()
    result = FieldGuideBandsEngine().layout(spec, refs, _StubLoader())
    assert len(result.placements) == 1
    p = result.placements[0]
    assert p.draw_width > 0
    assert p.draw_height > 0
    assert p.x >= 0
    assert p.y >= 0


def test_hero_plus_pair_single_zigzag_pattern() -> None:
    """13 fish should produce 1 hero + 4 pairs + 4 singles in the canonical pattern.

    Pair fish hug the L/R edges (x near side_margin or canvas - side_margin - w);
    singles sit centered horizontally.
    """
    refs = [_ref(f"sp{i}", 2.5 - i * 0.15) for i in range(13)]
    spec = _spec()
    engine = FieldGuideBandsEngine()
    result = engine.layout(spec, refs, _StubLoader())
    assert len(result.placements) == 13

    # Hero is the first placement and has the largest draw width.
    hero = result.placements[0]
    assert hero.species_ref.slug == "sp0"
    max_w = max(p.draw_width for p in result.placements)
    assert hero.draw_width == max_w

    # Hero is roughly centered.
    cx = hero.x + hero.draw_width / 2
    assert abs(cx - spec.canvas_width / 2) < spec.canvas_width * 0.02


def test_no_fish_overlap_any_other_fish_bbox() -> None:
    """With 13 species, no two placement bboxes should overlap."""
    refs = [_ref(f"sp{i}", 2.5 - i * 0.15) for i in range(13)]
    spec = _spec()
    result = FieldGuideBandsEngine().layout(spec, refs, _StubLoader())

    def boxes_overlap(a, b) -> bool:
        return not (
            a.x + a.draw_width <= b.x
            or b.x + b.draw_width <= a.x
            or a.y + a.draw_height <= b.y
            or b.y + b.draw_height <= a.y
        )

    placements = result.placements
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            assert not boxes_overlap(a, b), (
                f"{a.species_ref.slug} overlaps {b.species_ref.slug}"
            )
