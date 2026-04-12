"""Layout engine selector: picks the best-fit engine for a given species set."""
from __future__ import annotations

from poster_layout.engines import HeroLayoutEngine, PackedLayoutEngine
from poster_layout.interfaces import LayoutEngine
from poster_layout.models import PosterSpec, SpeciesRef


def select_layout_engine(
    species: list[SpeciesRef],
    spec: PosterSpec,
) -> LayoutEngine:
    """Return the best-fit layout engine for the given species + spec.

    Routing:
      - N=1   -> HeroLayoutEngine (single-subject editorial)
      - N>=2  -> PackedLayoutEngine (organic shelf-packing, no grid)
    """
    if len(species) <= 1:
        return HeroLayoutEngine()
    return PackedLayoutEngine()
