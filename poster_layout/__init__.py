"""wildprint poster_layout package — models, interfaces, and concrete engines."""
from __future__ import annotations

from poster_layout.models import (
    SpeciesRef,
    StyleRef,
    MasterImage,
    PosterSpec,
    LayoutResult,
    PlacedItem,
)
from poster_layout.interfaces import (
    PosterRenderer,
    MasterImageLoader,
    LayoutEngine,
)
from poster_layout.loader import FileSystemMasterImageLoader
from poster_layout.engines import (
    GridLayoutEngine,
    HeroLayoutEngine,
    PackedLayoutEngine,
    ScaledRowLayoutEngine,
    SmallEnsembleLayoutEngine,
)
from poster_layout.renderer import (
    EditorialMultiRenderer,
    EditorialPosterRenderer,
    PillowPosterRenderer,
)
from poster_layout.selector import select_layout_engine

__all__ = [
    "SpeciesRef",
    "StyleRef",
    "MasterImage",
    "PosterSpec",
    "LayoutResult",
    "PlacedItem",
    "PosterRenderer",
    "MasterImageLoader",
    "LayoutEngine",
    "FileSystemMasterImageLoader",
    "ScaledRowLayoutEngine",
    "GridLayoutEngine",
    "HeroLayoutEngine",
    "PackedLayoutEngine",
    "SmallEnsembleLayoutEngine",
    "PillowPosterRenderer",
    "EditorialPosterRenderer",
    "EditorialMultiRenderer",
    "select_layout_engine",
]
