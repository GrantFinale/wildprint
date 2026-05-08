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
    """A chosen master image for a (species, style) pair on disk.

    ``alpha_bbox`` is the tight pixel rectangle (x0, y0, x1, y1) that contains
    every pixel with meaningful (>30) alpha — i.e. the actual fish silhouette
    inside the (mostly-transparent) master canvas. The poster engine sizes
    each fish using the alpha-bbox aspect ratio (not the master canvas aspect)
    so a 3.4:1 northern pike doesn't get stuffed into a 1.5:1 bounding box
    and rendered tiny. Defaults to ``None`` (use full image), which keeps the
    legacy code paths and stub fixtures working unchanged.
    """

    species_slug: str
    style_slug: str
    image_path: Path
    width_px: int
    height_px: int
    alpha_bbox: tuple[int, int, int, int] | None = None

    @property
    def bbox_width_px(self) -> int:
        """Width of the alpha bbox, or full master width if no bbox."""
        if self.alpha_bbox is None:
            return self.width_px
        return max(1, self.alpha_bbox[2] - self.alpha_bbox[0])

    @property
    def bbox_height_px(self) -> int:
        """Height of the alpha bbox, or full master height if no bbox."""
        if self.alpha_bbox is None:
            return self.height_px
        return max(1, self.alpha_bbox[3] - self.alpha_bbox[1])

    @classmethod
    def from_file(
        cls,
        species_slug: str,
        style_slug: str,
        image_path: Path,
    ) -> "MasterImage":
        """Build a MasterImage by reading dimensions from the file on disk.

        Also computes the tight alpha bbox of the fish silhouette so the
        poster engine can size at true-fish aspect rather than master-canvas
        aspect.
        """
        from PIL import Image

        with Image.open(image_path) as img:
            width, height = img.size
            bbox = _compute_alpha_bbox(img)
        return cls(
            species_slug=species_slug,
            style_slug=style_slug,
            image_path=Path(image_path),
            width_px=int(width),
            height_px=int(height),
            alpha_bbox=bbox,
        )


def _compute_alpha_bbox(img: "object") -> tuple[int, int, int, int] | None:
    """Return the tight bbox of pixels with alpha>30, or None if fully opaque
    or fully transparent. Uses numpy for speed; falls back to PIL.getbbox()
    on import error.
    """
    try:
        import numpy as np
        from PIL import Image as _Image  # noqa: F401
    except ImportError:
        rgba = img.convert("RGBA") if img.mode != "RGBA" else img
        bbox = rgba.getchannel("A").point(lambda v: 255 if v > 30 else 0).getbbox()
        return tuple(bbox) if bbox else None
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]
    if alpha.min() >= 255:
        # No transparency — bbox = full image.
        return (0, 0, int(arr.shape[1]), int(arr.shape[0]))
    ys, xs = np.where(alpha > 30)
    if len(xs) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


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
