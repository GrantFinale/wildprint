"""Visual regression tests for FieldGuidePackedEngine (concept-2).

These tests pin down the **placement geometry** at three fish counts
(8 / 15 / 25) and one short-bodied panfish-heavy mix. We snapshot the
``(slug, x, y, draw_width, draw_height)`` tuple for every placement to
a JSON fixture; on subsequent runs, the engine output must match within
a tight tolerance.

Why placement snapshots, not pixel-diff (SSIM)
==============================================

Placement snapshots are:
- 100x faster (no PIL render in the test loop)
- Strictly deterministic (no PIL rendering randomness)
- More precise (catch single-pixel placement drift, not just gross visual change)
- Self-documenting (humans can read the JSON to see what changed)

If the test fails, regenerate the snapshot with::

    PYTHONPATH=. pytest tests/poster_layout/test_packed_visual_regression.py \\
        -v --regen-goldens

(The ``--regen-goldens`` flag is a pytest custom option; see ``conftest.py``.)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from poster_layout import FileSystemMasterImageLoader, PosterSpec
from poster_layout.engines_v3 import FieldGuidePackedEngine
from poster_layout import masks


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "packed_goldens"


@pytest.fixture
def regen_goldens(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--regen-goldens", default=False))


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    masks.clear_cache()
    yield
    masks.clear_cache()


def _build_refs(slugs: list[str]) -> list:
    """Hydrate SpeciesRefs from species.json."""
    from review_app.render.wildprint_renderer import _build_species_refs

    return _build_species_refs(slugs)


def _render_placements(slugs: list[str], cw: int = 5400, ch: int = 7200) -> list[dict]:
    """Run the v3 engine and return placement geometry as a list of dicts.

    Each entry: ``{slug, x, y, draw_width, draw_height}``. Sorted by
    ``(y, x, slug)`` so order is deterministic.
    """
    from config.settings import MASTER_DIR

    refs = _build_refs(slugs)
    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)
    spec = PosterSpec(
        title="Test Lake",
        subtitle=None,
        style_slug="scientific",
        species_slugs=slugs,
        layout_style="field_guide_packed",
        canvas_width=cw,
        canvas_height=ch,
        background_color="#FAF6EA",
    )
    result = FieldGuidePackedEngine().layout(spec, refs, loader)
    items = [
        {
            "slug": p.species_ref.slug,
            "x": int(p.x),
            "y": int(p.y),
            "draw_width": int(p.draw_width),
            "draw_height": int(p.draw_height),
        }
        for p in result.placements
    ]
    return sorted(items, key=lambda d: (d["y"], d["x"], d["slug"]))


def _compare_or_regen(
    name: str, slugs: list[str], regen: bool, tolerance_px: int = 24
) -> None:
    """Compare engine output against a JSON golden, or regenerate if requested.

    Tolerance: each numeric field (x, y, w, h) must match the golden to
    within ``tolerance_px`` pixels. This allows for harmless FFT-noise
    drift across scipy versions without losing regression coverage.
    The slug list and count must match EXACTLY.
    """
    fixture = FIXTURE_DIR / f"{name}.json"
    actual = _render_placements(slugs)

    if regen or not fixture.exists():
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        fixture.write_text(json.dumps(actual, indent=2) + "\n")
        if regen:
            print(f"REGENERATED: {fixture}")
        else:
            pytest.skip(f"created fixture {fixture}; re-run to verify")
        return

    expected = json.loads(fixture.read_text())

    # Slug set + count must match exactly.
    actual_slugs = sorted(p["slug"] for p in actual)
    expected_slugs = sorted(p["slug"] for p in expected)
    assert actual_slugs == expected_slugs, (
        f"slug mismatch for {name}:\n"
        f"  actual:   {actual_slugs}\n"
        f"  expected: {expected_slugs}"
    )

    # For each fish, find its expected placement and compare numerics.
    expected_by_slug = {p["slug"]: p for p in expected}
    drifts: list[str] = []
    for a in actual:
        e = expected_by_slug[a["slug"]]
        for field in ("x", "y", "draw_width", "draw_height"):
            if abs(a[field] - e[field]) > tolerance_px:
                drifts.append(
                    f"{a['slug']}.{field}: actual={a[field]} expected={e[field]} "
                    f"(diff={a[field] - e[field]})"
                )
    if drifts:
        msg = (
            f"\n{len(drifts)} placement drift(s) for {name} "
            f"(tolerance ±{tolerance_px}px):\n  "
            + "\n  ".join(drifts[:20])
        )
        if len(drifts) > 20:
            msg += f"\n  ... and {len(drifts) - 20} more"
        msg += f"\n\nRegen with: pytest {Path(__file__).name} --regen-goldens"
        pytest.fail(msg)


# ---- The four golden cases -------------------------------------------------


def test_golden_8_fish_balanced_mix(regen_goldens: bool) -> None:
    """Standard 8-fish mix (Cedar Pond). Representative non-stressful case."""
    slugs = [
        "northern_pike",
        "bowfin",
        "largemouth_bass",
        "smallmouth_bass",
        "black_crappie",
        "yellow_perch",
        "bluegill",
        "channel_catfish",
    ]
    _compare_or_regen("8_fish_balanced", slugs, regen_goldens)


def test_golden_long_bodied_predators(regen_goldens: bool) -> None:
    """8 long-bodied fish — stress test for vertical packing."""
    slugs = [
        "northern_pike",
        "bowfin",
        "channel_catfish",
        "common_carp",
        "white_sucker",
        "bullhead_catfish",
        "largemouth_bass",
        "smallmouth_bass",
    ]
    _compare_or_regen("long_bodied_predators", slugs, regen_goldens)


def test_golden_panfish_heavy(regen_goldens: bool) -> None:
    """8 short/fat-bodied panfish — stress test for horizontal packing."""
    slugs = [
        "northern_pike",  # hero (still need a hero)
        "bluegill",
        "pumpkinseed_sunfish",
        "rock_bass",
        "black_crappie",
        "yellow_perch",
        "largemouth_bass",
        "smallmouth_bass",
    ]
    _compare_or_regen("panfish_heavy", slugs, regen_goldens)


def test_golden_11_fish_with_hero_dominant(regen_goldens: bool) -> None:
    """11 fish with a clearly dominant pike hero — typical full-lake mix.

    (Trimmed from 25 since the catalog only has 13 fish masters; if more
    were available this'd be 25.)
    """
    slugs = [
        "northern_pike",
        "largemouth_bass",
        "smallmouth_bass",
        "rock_bass",
        "yellow_perch",
        "common_carp",
        "channel_catfish",
        "white_sucker",
        "pumpkinseed_sunfish",
        "bluegill",
        "bullhead_catfish",
    ]
    _compare_or_regen("11_fish_full_lake", slugs, regen_goldens)
