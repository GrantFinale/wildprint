"""Dataclass models consumed by the future wildprint poster engine.

These records are intentionally minimal and decoupled from the generation
pipeline. The poster engine reads master images off disk and lays them out
using species metadata + a poster spec; it does not care how the masters
were produced.
"""
from __future__ import annotations


from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SpeciesRef:
    """A reference to a single species in the wildprint catalog.

    Attributes:
        slug: Stable identifier (e.g. "smallmouth_bass").
        common_name: Human-readable common name.
        scientific_name: Binomial scientific name.
        category: Top-level grouping (e.g. "fish", "bird", "turtle").
        relative_scale_index: Style-agnostic relative size — ratio of this
            species' typical adult body length to the Smallmouth Bass
            baseline (1.0). The same value is used across scientific,
            watercolor, and vintage_engraving variants so the poster engine
            can render Northern Pike at ~2.5x the length of Smallmouth Bass
            regardless of the source image's pixel dimensions.
        habitat_tags: Free-form habitat descriptors (e.g. ["lake", "river"]).
    """

    slug: str
    common_name: str
    scientific_name: str
    category: str
    relative_scale_index: float
    habitat_tags: list[str]


@dataclass(frozen=True)
class StyleRef:
    """A reference to a single illustration style."""

    slug: str
    style_name: str
    description: str


@dataclass(frozen=True)
class MasterImage:
    """A chosen master image for a (species, style) pair on disk."""

    species_slug: str
    style_slug: str
    image_path: Path
    width_px: int
    height_px: int

    @classmethod
    def from_file(
        cls,
        species_slug: str,
        style_slug: str,
        image_path: Path,
    ) -> "MasterImage":
        """Build a MasterImage by reading dimensions from the file on disk."""
        from PIL import Image

        with Image.open(image_path) as img:
            width, height = img.size
        return cls(
            species_slug=species_slug,
            style_slug=style_slug,
            image_path=Path(image_path),
            width_px=int(width),
            height_px=int(height),
        )


@dataclass(frozen=True)
class PosterSpec:
    """Declarative description of a poster the user wants to render."""

    title: str
    subtitle: str | None
    style_slug: str
    species_slugs: list[str]
    layout_style: str
    canvas_width: int
    canvas_height: int
    background_color: str = "#FFFFFF"
    show_labels: bool = True


@dataclass(frozen=True)
class PlacedItem:
    """A single species illustration positioned on the poster canvas."""

    species_ref: SpeciesRef
    master: MasterImage
    x: int
    y: int
    draw_width: int
    draw_height: int


@dataclass
class LayoutResult:
    """Output of a LayoutEngine: the spec plus computed placements."""

    poster: PosterSpec
    placements: list[PlacedItem]
    warnings: list[str] = field(default_factory=list)
