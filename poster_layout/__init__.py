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
    FieldGuideBandsEngine,
    GridLayoutEngine,
    HeroLayoutEngine,
    PackedLayoutEngine,
    ScaledRowLayoutEngine,
    SilhouettePackedLayoutEngine,
    SmallEnsembleLayoutEngine,
    VintageCatalogEngine,
)
from poster_layout.renderer import (
    EditorialMultiRenderer,
    EditorialPosterRenderer,
    PillowPosterRenderer,
)
from poster_layout.selector import select_layout_engine
from poster_layout.style_profiles import (
    FIELD_GUIDE_PROFILE,
    LEGACY_PROFILE,
    StyleProfile,
    VINTAGE_TACKLE_PROFILE,
    get_profile,
)

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
    "SilhouettePackedLayoutEngine",
    "SmallEnsembleLayoutEngine",
    "FieldGuideBandsEngine",
    "VintageCatalogEngine",
    "PillowPosterRenderer",
    "EditorialPosterRenderer",
    "EditorialMultiRenderer",
    "select_layout_engine",
    "StyleProfile",
    "FIELD_GUIDE_PROFILE",
    "VINTAGE_TACKLE_PROFILE",
    "LEGACY_PROFILE",
    "get_profile",
]
