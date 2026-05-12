"""Layout engine selector: picks the best-fit engine for a given species set."""
from __future__ import annotations

from poster_layout.engines import (
    FieldGuideBandsEngine,
    HeroLayoutEngine,
    PackedLayoutEngine,
    SilhouettePackedLayoutEngine,
    VintageCatalogEngine,
)
from poster_layout.engines_v3 import FieldGuidePackedEngine
from poster_layout.interfaces import LayoutEngine
from poster_layout.models import PosterSpec, SpeciesRef


def select_layout_engine(
    species: list[SpeciesRef],
    spec: PosterSpec,
) -> LayoutEngine:
    """Return the best-fit layout engine for the given species + spec.

    Routing precedence (first match wins):
      1. ``layout_style == "field_guide"`` -> FieldGuideBandsEngine v2
         (honest-scale band packing, the reference field-guide aesthetic).
      2. ``layout_style == "field_guide_packed"`` -> FieldGuidePackedEngine v3
         (irregular-shape pixel-mask packing — concept-2; see engines_v3.py).
      3. ``layout_style == "vintage_tackle"`` -> VintageCatalogEngine
         (uniform 4-column grid, antique sporting-goods catalog look).
      4. ``layout_style == "hero"`` OR len(species) == 1 -> HeroLayoutEngine
         (single-subject editorial).
      5. ``layout_style == "packed"`` -> PackedLayoutEngine (bbox shelves).
      6. Otherwise -> SilhouettePackedLayoutEngine (alpha-aware packing,
         the historical default for multi-species posters).
    """
    style = (spec.layout_style or "").strip().lower()
    if style == "field_guide":
        return FieldGuideBandsEngine()
    if style == "field_guide_packed":
        return FieldGuidePackedEngine()
    if style == "vintage_tackle":
        return VintageCatalogEngine()
    if style == "hero" or len(species) <= 1:
        return HeroLayoutEngine()
    if style == "packed":
        return PackedLayoutEngine()
    return SilhouettePackedLayoutEngine()
