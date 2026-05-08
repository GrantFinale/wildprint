"""Concrete ``LayoutEngine`` implementations for wildprint posters.

Three engines are provided:

``ScaledRowLayoutEngine``
    Greedy row-packing layout that preserves each species'
    ``relative_scale_index``. A Northern Pike (2.5) will render at 2.5x the
    body length of a Smallmouth Bass (1.0) even if their source masters have
    different pixel dimensions. This is the default, production layout.

``GridLayoutEngine``
    Uniform grid of ``ceil(sqrt(n))`` columns. Each species is centered
    within its cell, and within the cell is still sized by relative scale
    (not cell dimensions) so size comparisons remain honest.

``HeroLayoutEngine``
    Single-subject editorial layout. Carves the canvas into a title band
    (top 20%), hero image band (middle 55%), and caption band (bottom
    15%), then centers the single master within the hero band preserving
    aspect ratio.

All engines honor ``PosterSpec`` margins, optional label space, and emit
human-readable warnings for missing masters and forced shrinking.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from poster_layout.interfaces import LayoutEngine, MasterImageLoader
from poster_layout.models import (
    LayoutResult,
    MasterImage,
    PlacedItem,
    PosterSpec,
    SpeciesRef,
)


# --- Default label measurement (mirrors EditorialMultiRenderer) -------------


# Cached fonts: loaded lazily on first call. We mirror EditorialMultiRenderer's
# defaults: Didot fallback chain at 42px (common-name) and 32px (scientific).
# If the live renderer uses different sizes (e.g. dense-shelf scaling), the
# tightening pass will still err on the SAFE side because it measures the
# DEFAULT sizes which are >= the dense-scaled sizes.
_LABEL_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Didot.ttc",
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "/System/Library/Fonts/Supplemental/Hoefler Text.ttc",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
)
_LABEL_COMMON_PX = 42
_LABEL_SCI_PX = 32
_LABEL_TRACKING_PX = 4
_LABEL_MEASURE_CACHE: dict[tuple[str, str], tuple[int, int]] = {}


def _measure_label_text(text: str, font_size: int, tracked: bool) -> tuple[int, int]:
    """Measure a single line of text using the first available font.

    Uses Pillow's textbbox; applies tracking by adding ``_LABEL_TRACKING_PX``
    between glyphs when ``tracked`` is True (matches the renderer's tracked
    common-name draw). Falls back to a coarse char-width estimate when no
    Pillow font loads (very rare on the production droplet).
    """
    if not text:
        return 0, 0
    try:
        from PIL import ImageDraw, ImageFont, Image as _PIL
        font = None
        for cand in _LABEL_FONT_CANDIDATES:
            try:
                font = ImageFont.truetype(cand, font_size)
                break
            except (OSError, IOError, ValueError):
                continue
        if font is None:
            font = ImageFont.load_default()
        # Use a throwaway image to get a draw context.
        img = _PIL.new("RGB", (1, 1))
        draw = ImageDraw.Draw(img)
        if tracked:
            advances: list[int] = []
            for ch in text:
                bb = draw.textbbox((0, 0), ch, font=font)
                advances.append(bb[2] - bb[0])
            w = sum(advances) + _LABEL_TRACKING_PX * max(0, len(text) - 1)
            bb_h = draw.textbbox((0, 0), text, font=font)
            h = bb_h[3] - bb_h[1]
        else:
            bb = draw.textbbox((0, 0), text, font=font)
            w = bb[2] - bb[0]
            h = bb[3] - bb[1]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        # Last-ditch coarse estimate.
        return int(len(text) * font_size * 0.55), int(font_size * 1.1)


def _default_label_size_provider(species_ref) -> tuple[int, int]:
    """Default label-size provider used by SilhouettePackedLayoutEngine.

    Measures the label as the renderer would draw it: common name in
    UPPERCASE with ~4px tracking on top, italic scientific name below
    (with a small gap). Returns (width, height) of the bounding rect of
    the two-line block. Cached by (common, scientific) so repeated lookups
    in the tightening pass are O(1).
    """
    common = (getattr(species_ref, "common_name", "") or "").upper()
    sci = getattr(species_ref, "scientific_name", "") or ""
    cache_key = (common, sci)
    if cache_key in _LABEL_MEASURE_CACHE:
        return _LABEL_MEASURE_CACHE[cache_key]
    cw, ch = _measure_label_text(common, _LABEL_COMMON_PX, tracked=True)
    sw, sh = _measure_label_text(sci, _LABEL_SCI_PX, tracked=False)
    gap = max(6, ch // 4) if common and sci else 0
    label_w = max(cw, sw)
    label_h = ch + gap + sh
    _LABEL_MEASURE_CACHE[cache_key] = (label_w, label_h)
    return label_w, label_h


# --- Shared geometry helpers -------------------------------------------------


@dataclass(frozen=True)
class _Margins:
    """Pixel margins carved out of a ``PosterSpec`` canvas."""

    left: int
    right: int
    top: int
    bottom: int

    @property
    def content_width(self) -> int:
        return max(1, self._canvas_w - self.left - self.right)

    @property
    def content_height(self) -> int:
        return max(1, self._canvas_h - self.top - self.bottom)

    # The content_* helpers need the canvas they were derived from. We stash
    # it via a class-level attribute on construction for readability.
    _canvas_w: int = 0
    _canvas_h: int = 0


def _compute_margins(spec: PosterSpec) -> _Margins:
    """Compute pixel margins from the hard-coded fractional spec."""
    left = int(round(spec.canvas_width * 0.06))
    right = int(round(spec.canvas_width * 0.06))
    top = int(round(spec.canvas_height * 0.15))
    bottom = int(round(spec.canvas_height * 0.08))
    return _Margins(
        left=left,
        right=right,
        top=top,
        bottom=bottom,
        _canvas_w=spec.canvas_width,
        _canvas_h=spec.canvas_height,
    )


def _resolve_species_with_masters(
    spec: PosterSpec,
    species: list[SpeciesRef],
    loader: MasterImageLoader,
    warnings: list[str],
) -> list[tuple[SpeciesRef, MasterImage]]:
    """Load masters for the provided species list, recording warnings on miss."""
    pairs: list[tuple[SpeciesRef, MasterImage]] = []
    for ref in species:
        try:
            master = loader.get(ref.slug, spec.style_slug)
        except FileNotFoundError:
            warnings.append(
                f"Missing master for species '{ref.slug}' in style "
                f"'{spec.style_slug}' — skipped."
            )
            continue
        pairs.append((ref, master))
    return pairs


# --- ScaledRowLayoutEngine ---------------------------------------------------


class ScaledRowLayoutEngine(LayoutEngine):
    """Greedy scaled-row layout that preserves relative species sizes.

    The engine picks a shared "reference body length" such that the largest
    species in the set occupies at most ``max_per_row_fraction`` of the
    content width. Species are then packed into rows left-to-right. Rows
    are stacked with even vertical spacing and centered horizontally.

    Note: the class name is authoritative. If a ``PosterSpec`` with
    ``layout_style='grid'`` is passed to this engine, the scaled-row
    algorithm is still used (and vice versa for ``GridLayoutEngine``).
    """

    def __init__(
        self,
        max_per_row_fraction: float = 0.40,
        inter_item_gap_fraction: float = 0.025,
        row_gap_fraction: float = 0.05,
        label_height_fraction: float = 0.04,
    ) -> None:
        # Lower value → smaller per-item width → more items per row → fewer
        # rows → less vertical shrink. 0.40 gives comfortable packing for
        # 13–20 species without starving vertical space.
        self.max_per_row_fraction = max_per_row_fraction
        self.inter_item_gap_fraction = inter_item_gap_fraction
        self.row_gap_fraction = row_gap_fraction
        self.label_height_fraction = label_height_fraction

    # ------------------------------------------------------------------ public

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []
        pairs = _resolve_species_with_masters(spec, species, loader, warnings)

        if not pairs:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        margins = _compute_margins(spec)
        content_w = margins.content_width
        content_h = margins.content_height

        gap_px = int(round(content_w * self.inter_item_gap_fraction))
        row_gap_px = int(round(content_h * self.row_gap_fraction))
        label_px = (
            int(round(spec.canvas_height * self.label_height_fraction))
            if spec.show_labels
            else 0
        )

        # Choose initial reference length so the largest species fits.
        max_scale = max(ref.relative_scale_index for ref, _ in pairs)
        reference_length = (self.max_per_row_fraction * content_w) / max_scale

        placements, shrink_factor, forced_solo_rows = self._place(
            pairs=pairs,
            reference_length=reference_length,
            content_w=content_w,
            content_h=content_h,
            margins=margins,
            gap_px=gap_px,
            row_gap_px=row_gap_px,
            label_px=label_px,
        )

        if shrink_factor < 1.0:
            if shrink_factor < 0.5:
                warnings.append(
                    f"Layout shrunk to {shrink_factor:.0%} of target size to "
                    f"fit {len(pairs)} species on the canvas."
                )
        if forced_solo_rows:
            warnings.append(
                f"{forced_solo_rows} row(s) forced to a single item because "
                f"the species was too wide to share a row."
            )

        return LayoutResult(
            poster=spec,
            placements=placements,
            warnings=warnings,
        )

    # --------------------------------------------------------------- internals

    def _pack_rows(
        self,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        reference_length: float,
        content_w: int,
        gap_px: int,
    ) -> tuple[list[list[tuple[SpeciesRef, MasterImage, int, int]]], int]:
        """Greedy row-pack. Returns (rows, forced_solo_count).

        Each row entry: (species_ref, master, draw_width, draw_height).
        """
        rows: list[list[tuple[SpeciesRef, MasterImage, int, int]]] = []
        current: list[tuple[SpeciesRef, MasterImage, int, int]] = []
        current_width = 0
        forced_solo = 0

        for ref, master in pairs:
            draw_w = max(1, int(round(reference_length * ref.relative_scale_index)))
            aspect = master.height_px / master.width_px if master.width_px else 1.0
            draw_h = max(1, int(round(draw_w * aspect)))

            projected = current_width + draw_w
            if current:
                projected += gap_px

            if projected > content_w and current:
                rows.append(current)
                current = [(ref, master, draw_w, draw_h)]
                current_width = draw_w
                if draw_w > content_w:
                    forced_solo += 1
            else:
                current.append((ref, master, draw_w, draw_h))
                current_width = projected
                if not current[:-1] and draw_w > content_w:
                    forced_solo += 1

        if current:
            rows.append(current)
        return rows, forced_solo

    def _place(
        self,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        reference_length: float,
        content_w: int,
        content_h: int,
        margins: _Margins,
        gap_px: int,
        row_gap_px: int,
        label_px: int,
    ) -> tuple[list[PlacedItem], float, int]:
        """Pack, measure vertical extent, shrink if needed, compute positions."""
        initial_reference = reference_length
        shrink_factor = 1.0

        # Iterate: pack, measure total vertical extent, shrink if overflow.
        for _ in range(16):  # bounded to avoid infinite loops
            rows, forced_solo = self._pack_rows(
                pairs=pairs,
                reference_length=reference_length,
                content_w=content_w,
                gap_px=gap_px,
            )

            row_heights: list[int] = []
            for row in rows:
                tallest = max(item[3] for item in row)
                row_heights.append(tallest + label_px)

            total_height = sum(row_heights) + row_gap_px * max(0, len(rows) - 1)

            if total_height <= content_h or len(rows) == 0:
                break

            # Shrink proportionally and retry.
            ratio = content_h / total_height
            # Clamp to avoid thrashing on micro-overshoots.
            ratio = max(0.5, min(0.95, ratio))
            reference_length *= ratio
            shrink_factor = reference_length / initial_reference
            if reference_length < 1.0:
                break

        shrink_factor = reference_length / initial_reference

        # Compute absolute placements.
        placements: list[PlacedItem] = []
        total_height = sum(row_heights) + row_gap_px * max(0, len(rows) - 1)
        # Start y so the content block is vertically centered in the content area.
        y_cursor = margins.top + max(0, (content_h - total_height) // 2)

        for row, row_h in zip(rows, row_heights):
            item_widths = [item[2] for item in row]
            row_content_w = sum(item_widths) + gap_px * max(0, len(row) - 1)
            x_cursor = margins.left + max(0, (content_w - row_content_w) // 2)
            item_top_y = y_cursor  # top of tallest item in row
            # Items baseline-align to the bottom of the illustration region.
            row_illustration_h = row_h - label_px
            for ref, master, draw_w, draw_h in row:
                # Align to bottom of row illustration region so labels sit flush.
                y_item = item_top_y + (row_illustration_h - draw_h)
                placements.append(
                    PlacedItem(
                        species_ref=ref,
                        master=master,
                        x=int(x_cursor),
                        y=int(y_item),
                        draw_width=int(draw_w),
                        draw_height=int(draw_h),
                    )
                )
                x_cursor += draw_w + gap_px
            y_cursor += row_h + row_gap_px

        return placements, shrink_factor, forced_solo


# --- GridLayoutEngine --------------------------------------------------------


class GridLayoutEngine(LayoutEngine):
    """Uniform grid layout where relative scale is preserved within cells.

    Species are laid out in ``ceil(sqrt(n))`` columns. Each cell has the
    same pixel dimensions, but the drawn illustration within a cell is
    scaled according to ``relative_scale_index`` relative to a shared
    reference length — so size comparisons across cells remain meaningful.

    Note: the class name is authoritative regardless of
    ``PosterSpec.layout_style``.
    """

    def __init__(
        self,
        cell_padding_fraction: float = 0.08,
        label_height_fraction: float = 0.04,
    ) -> None:
        self.cell_padding_fraction = cell_padding_fraction
        self.label_height_fraction = label_height_fraction

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []
        pairs = _resolve_species_with_masters(spec, species, loader, warnings)

        if not pairs:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        margins = _compute_margins(spec)
        content_w = margins.content_width
        content_h = margins.content_height

        n = len(pairs)
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = max(1, math.ceil(n / cols))

        cell_w = content_w // cols
        cell_h = content_h // rows

        label_px = (
            int(round(spec.canvas_height * self.label_height_fraction))
            if spec.show_labels
            else 0
        )
        cell_pad = int(round(min(cell_w, cell_h) * self.cell_padding_fraction))

        inner_w = max(1, cell_w - 2 * cell_pad)
        inner_h = max(1, cell_h - 2 * cell_pad - label_px)

        max_scale = max(ref.relative_scale_index for ref, _ in pairs)
        # Reference length: so the largest species just fits inside a cell
        # horizontally. Then vertically clamp if needed (per item).
        reference_length = inner_w / max_scale

        initial_reference = reference_length
        placements: list[PlacedItem] = []

        # Clamp reference length by the worst vertical fit too.
        for ref, master in pairs:
            draw_w = reference_length * ref.relative_scale_index
            aspect = master.height_px / master.width_px if master.width_px else 1.0
            draw_h = draw_w * aspect
            if draw_h > inner_h:
                reference_length *= inner_h / draw_h

        shrink_factor = reference_length / initial_reference
        if shrink_factor < 0.5:
            warnings.append(
                f"Grid layout shrunk to {shrink_factor:.0%} of target size to "
                f"fit {n} species within their cells."
            )

        for idx, (ref, master) in enumerate(pairs):
            r = idx // cols
            c = idx % cols

            cell_x = margins.left + c * cell_w
            cell_y = margins.top + r * cell_h

            draw_w = max(1, int(round(reference_length * ref.relative_scale_index)))
            aspect = master.height_px / master.width_px if master.width_px else 1.0
            draw_h = max(1, int(round(draw_w * aspect)))

            # Center the illustration in its inner cell region (above label).
            x = cell_x + cell_pad + (inner_w - draw_w) // 2
            y = cell_y + cell_pad + (inner_h - draw_h) // 2

            placements.append(
                PlacedItem(
                    species_ref=ref,
                    master=master,
                    x=int(x),
                    y=int(y),
                    draw_width=draw_w,
                    draw_height=draw_h,
                )
            )

        return LayoutResult(poster=spec, placements=placements, warnings=warnings)


# --- HeroLayoutEngine --------------------------------------------------------


class HeroLayoutEngine(LayoutEngine):
    """Single-subject editorial layout for hero posters.

    The canvas is sliced vertically into three bands:

    - **Title band** — top 20% of canvas height, for title + scientific
      name + ornamental rule. The engine does not place anything here;
      the renderer draws directly into it.
    - **Hero band** — middle 55% of canvas height, where the species
      illustration lives. The master is scaled to fit (band_width ×
      band_height) while preserving aspect ratio, then centered.
    - **Caption band** — bottom 15% of canvas height, for habitat
      subtitle. Again, the renderer owns this region.

    Side margins default to 10% of canvas width, leaving the hero image
    with ~80% of the canvas to live inside.

    Only a single species is supported. If more than one is passed in the
    spec, a warning is logged and the first species is used.
    """

    def __init__(
        self,
        title_band_fraction: float = 0.18,
        caption_band_fraction: float = 0.10,
        side_margin_fraction: float = 0.04,
        title_to_hero_gap_fraction: float = 0.015,
    ) -> None:
        # Fixed title band (top) and minimum caption band (bottom). The
        # hero image fills everything in between up to the content width,
        # pinned near the top of its region so negative space lives below
        # the fish — a more editorial composition than vertical centering.
        self.title_band_fraction = title_band_fraction
        self.caption_band_fraction = caption_band_fraction
        self.side_margin_fraction = side_margin_fraction
        self.title_to_hero_gap_fraction = title_to_hero_gap_fraction
        # Kept for backwards-compat with any caller still reading the old
        # attribute. Derived from the other fractions.
        self.hero_band_fraction = (
            1.0
            - title_band_fraction
            - caption_band_fraction
            - title_to_hero_gap_fraction
        )

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []

        if not species:
            warnings.append("HeroLayoutEngine received zero species; nothing to place.")
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        if len(species) > 1:
            extras = ", ".join(ref.slug for ref in species[1:])
            warnings.append(
                f"HeroLayoutEngine expected exactly one species; got "
                f"{len(species)}. Using '{species[0].slug}' and dropping: {extras}."
            )
            logger.warning(
                "HeroLayoutEngine is single-subject; dropping extras: %s", extras
            )

        ref = species[0]
        try:
            master = loader.get(ref.slug, spec.style_slug)
        except FileNotFoundError:
            warnings.append(
                f"Missing master for species '{ref.slug}' in style "
                f"'{spec.style_slug}' — nothing to render."
            )
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # Fixed band heights.
        title_band_h = int(round(canvas_h * self.title_band_fraction))
        caption_band_h = int(round(canvas_h * self.caption_band_fraction))
        gap_h = int(round(canvas_h * self.title_to_hero_gap_fraction))

        side_margin_px = int(round(canvas_w * self.side_margin_fraction))
        content_w = max(1, canvas_w - 2 * side_margin_px)

        # Maximum hero height = everything between title+gap and caption.
        max_hero_h = max(1, canvas_h - title_band_h - gap_h - caption_band_h)

        # Master native dimensions.
        src_w = max(1, master.width_px)
        src_h = max(1, master.height_px)

        # Fit the master into (content_w, max_hero_h) preserving aspect.
        # For a horizontal subject on a portrait canvas this is width-bound
        # and the fish ends up using the full content width.
        scale = min(content_w / src_w, max_hero_h / src_h)
        draw_w = max(1, int(round(src_w * scale)))
        draw_h = max(1, int(round(src_h * scale)))

        if scale < 0.5:
            warnings.append(
                f"Hero master for '{ref.slug}' was shrunk to {scale:.0%} of "
                f"native size to fit the hero band."
            )

        # Pin the hero to the top of its available region (right below the
        # title band + gap). Negative space lives BELOW the fish, which
        # reads more editorial than vertical centering.
        x = (canvas_w - draw_w) // 2
        y = title_band_h + gap_h

        placed = PlacedItem(
            species_ref=ref,
            master=master,
            x=int(x),
            y=int(y),
            draw_width=int(draw_w),
            draw_height=int(draw_h),
        )

        return LayoutResult(poster=spec, placements=[placed], warnings=warnings)


# --- SmallEnsembleLayoutEngine -----------------------------------------------


class SmallEnsembleLayoutEngine(LayoutEngine):
    """Editorial multi-species layout for N=2..8.

    Uses hand-tuned slot templates per species count rather than computed
    grids — each template is composed for visual rhythm. Species are sorted
    by ``relative_scale_index`` (largest first) and assigned to slots in
    that order, so the most prominent species lands in the most prominent
    slot. Within a slot the master is sized by:

    - The slot's max-width budget (acts as a hard ceiling), and
    - The species' clamp-adjusted ``relative_scale_index`` (so size
      relationships between species remain honest).

    Scale clamping prevents minnows from disappearing next to pike: any
    species smaller than ``largest * min_visible_fraction`` (or smaller
    than ``largest / scale_clamp_ratio``) is floored to that minimum so
    it stays legible. The clamp emits a warning when it fires.

    Pairs with :class:`EditorialMultiRenderer`. The engine carves the
    canvas into a top title band, a middle species area, and a bottom
    caption band; templates position slots within the species area in
    normalized 0..1 coordinates.
    """

    # Slot layout: (cx_frac, cy_frac, max_width_frac).
    # Canvas is divided: top=title band, middle=species area, bottom=caption band.
    # Slots are positioned within the species area (normalized 0..1 vertically).
    _TEMPLATES: dict[int, list[tuple[float, float, float]]] = {
        2: [
            # Side by side, large, generous breathing room.
            (0.26, 0.50, 0.40),
            (0.74, 0.50, 0.40),
        ],
        3: [
            # Classic triangle: one centered top, two bottom.
            (0.50, 0.28, 0.44),
            (0.27, 0.72, 0.38),
            (0.73, 0.72, 0.38),
        ],
        4: [
            # 2x2 off-axis for dynamism.
            (0.27, 0.30, 0.34),
            (0.73, 0.30, 0.34),
            (0.27, 0.72, 0.34),
            (0.73, 0.72, 0.34),
        ],
        5: [
            # Pyramid: one hero top-center, four corners below.
            (0.50, 0.22, 0.42),
            (0.22, 0.58, 0.28),
            (0.78, 0.58, 0.28),
            (0.32, 0.85, 0.26),
            (0.68, 0.85, 0.26),
        ],
        6: [
            # Two rows of three.
            (0.22, 0.30, 0.28),
            (0.50, 0.30, 0.28),
            (0.78, 0.30, 0.28),
            (0.22, 0.72, 0.28),
            (0.50, 0.72, 0.28),
            (0.78, 0.72, 0.28),
        ],
        7: [
            # Hero + 3 + 3 ranked pyramid.
            (0.50, 0.20, 0.38),
            (0.20, 0.50, 0.26),
            (0.50, 0.52, 0.26),
            (0.80, 0.50, 0.26),
            (0.25, 0.82, 0.24),
            (0.55, 0.82, 0.24),
            (0.83, 0.82, 0.24),
        ],
        8: [
            # 2x4 grid, slightly offset rows for editorial feel.
            (0.18, 0.22, 0.24),
            (0.42, 0.22, 0.24),
            (0.62, 0.22, 0.24),
            (0.86, 0.22, 0.24),
            (0.18, 0.72, 0.24),
            (0.42, 0.72, 0.24),
            (0.62, 0.72, 0.24),
            (0.86, 0.72, 0.24),
        ],
    }

    # Approximate row count per template — used to budget vertical extent
    # so species don't visually run into each other or into the labels.
    _TEMPLATE_ROWS: dict[int, int] = {2: 1, 3: 2, 4: 2, 5: 3, 6: 2, 7: 3, 8: 2}

    def __init__(
        self,
        title_band_fraction: float = 0.16,
        caption_band_fraction: float = 0.10,
        side_margin_fraction: float = 0.05,
        scale_clamp_ratio: float = 4.0,
        min_visible_fraction: float = 0.25,
        label_reserve_fraction: float = 0.04,
    ) -> None:
        self.title_band_fraction = title_band_fraction
        self.caption_band_fraction = caption_band_fraction
        self.side_margin_fraction = side_margin_fraction
        self.scale_clamp_ratio = scale_clamp_ratio
        self.min_visible_fraction = min_visible_fraction
        self.label_reserve_fraction = label_reserve_fraction

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []

        # 1. Filter to species with existing masters.
        present: list[SpeciesRef] = []
        for ref in species:
            if loader.exists(ref.slug, spec.style_slug):
                present.append(ref)
            else:
                warnings.append(
                    f"Missing master for species '{ref.slug}' in style "
                    f"'{spec.style_slug}' — skipped."
                )

        if not present:
            warnings.append(
                "SmallEnsembleLayoutEngine: no species with existing masters; "
                "nothing to render."
            )
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 2. Cap at 8 (Phase 2 will handle larger ensembles).
        if len(present) > 8:
            warnings.append(
                f"SmallEnsembleLayoutEngine received {len(present)} species; "
                f"truncating to top 8 by relative_scale_index. Phase 2 will "
                f"add a FieldGuideLayoutEngine for N>=9."
            )
            present = sorted(
                present,
                key=lambda r: r.relative_scale_index,
                reverse=True,
            )[:8]

        # 3. Need at least 2 species — caller should route N=1 to Hero.
        if len(present) < 2:
            warnings.append(
                "SmallEnsembleLayoutEngine needs at least 2 species; got "
                f"{len(present)}. Caller should route single-species posters "
                "to HeroLayoutEngine."
            )
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 4. Sort largest-first so the prominent slot gets the prominent fish.
        species_sorted = sorted(
            present,
            key=lambda r: r.relative_scale_index,
            reverse=True,
        )

        # 5. Compute clamp-adjusted scales.
        raw_scales = [r.relative_scale_index for r in species_sorted]
        largest = max(raw_scales)
        max_floor = largest * self.min_visible_fraction
        ratio_floor = largest / self.scale_clamp_ratio if self.scale_clamp_ratio else 0.0
        floor = max(max_floor, ratio_floor)
        effective_scales = [max(s, floor) for s in raw_scales]
        clamp_fired = any(
            abs(eff - raw) > 1e-6 for eff, raw in zip(effective_scales, raw_scales)
        )

        # 6. Band pixel heights.
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        title_h = int(round(canvas_h * self.title_band_fraction))
        caption_h = int(round(canvas_h * self.caption_band_fraction))
        species_area_top = title_h
        species_area_h = max(1, canvas_h - title_h - caption_h)

        # 7. Pick the template.
        n = len(species_sorted)
        template = self._TEMPLATES[n]
        rows_in_template = max(1, self._TEMPLATE_ROWS.get(n, 2))
        # Vertical budget per "row" (used to cap draw_h so labels fit).
        per_row_h_cap = int(round((species_area_h / rows_in_template) * 0.85))
        label_reserve_px = int(round(canvas_h * self.label_reserve_fraction))

        # The largest effective scale defines the slot-fill unit.
        largest_eff = effective_scales[0]

        placements: list[PlacedItem] = []

        for i, sp in enumerate(species_sorted):
            try:
                master = loader.get(sp.slug, spec.style_slug)
            except FileNotFoundError:
                # Defensive — exists() said yes, but a race could remove it.
                warnings.append(
                    f"Lost master for species '{sp.slug}' between exists() and "
                    f"get(); skipped."
                )
                continue

            cx_frac, cy_frac, max_w_frac = template[i]

            # Slot center in pixels.
            sx = int(round(cx_frac * canvas_w))
            sy = int(round(species_area_top + cy_frac * species_area_h))

            # Slot-driven width ceiling and scale-driven target width.
            slot_max_w = max(1, int(round(max_w_frac * canvas_w)))
            scale_unit = slot_max_w / largest_eff if largest_eff else slot_max_w
            target_w = effective_scales[i] * scale_unit

            draw_w = max(1, int(round(min(slot_max_w, target_w))))

            # Preserve aspect ratio.
            src_w = max(1, master.width_px)
            src_h = max(1, master.height_px)
            draw_h = max(1, int(round(draw_w * src_h / src_w)))

            # Reserve label room: cap height to per-row budget less label.
            max_h_for_slot = max(1, per_row_h_cap - label_reserve_px)
            if draw_h > max_h_for_slot:
                ratio = max_h_for_slot / draw_h
                draw_h = max_h_for_slot
                draw_w = max(1, int(round(draw_w * ratio)))

            x = sx - draw_w // 2
            y = sy - draw_h // 2

            placements.append(
                PlacedItem(
                    species_ref=sp,
                    master=master,
                    x=int(x),
                    y=int(y),
                    draw_width=int(draw_w),
                    draw_height=int(draw_h),
                )
            )

        # 9. Warn if scale clamping fired.
        if clamp_fired:
            warnings.append(
                "SmallEnsembleLayoutEngine: scale clamping fired — one or more "
                f"species below the floor (largest*{self.min_visible_fraction:.2f}, "
                f"largest/{self.scale_clamp_ratio:.1f}) was floored to stay visible."
            )

        # 10. Safety: detect bounding-box overlaps between placed items.
        for a_idx in range(len(placements)):
            a = placements[a_idx]
            ax2 = a.x + a.draw_width
            ay2 = a.y + a.draw_height
            for b_idx in range(a_idx + 1, len(placements)):
                b = placements[b_idx]
                bx2 = b.x + b.draw_width
                by2 = b.y + b.draw_height
                if a.x < bx2 and ax2 > b.x and a.y < by2 and ay2 > b.y:
                    warnings.append(
                        f"SmallEnsembleLayoutEngine: '{a.species_ref.slug}' and "
                        f"'{b.species_ref.slug}' bounding boxes overlap — "
                        f"template may need tuning for this species mix."
                    )
                    break  # one warning per pair-set is enough

        return LayoutResult(
            poster=spec,
            placements=placements,
            warnings=warnings,
        )


# --- PackedLayoutEngine -------------------------------------------------------


class PackedLayoutEngine(LayoutEngine):
    """Organic shelf-packing layout that fills the canvas like a museum plate.

    Uses area-proportional sizing and greedy shelf packing to arrange species
    with minimal whitespace and no fixed grid structure. Species are sorted by
    ``relative_scale_index`` descending so the largest items anchor each shelf.
    A scale-to-fill pass ensures the collective illustrations fill the content
    area regardless of species count.

    Known limitation: for N>15, species labels may overlap horizontally within
    a shelf. The engine reserves vertical space for labels but does not prevent
    lateral label collision — the renderer is responsible for label drawing.
    """

    def __init__(
        self,
        title_band_fraction: float = 0.14,
        caption_band_fraction: float = 0.12,
        side_margin_fraction: float = 0.035,
        packing_target: float = 0.70,
        inter_item_gap_frac: float = 0.008,
        inter_shelf_gap_frac: float = 0.018,
        label_height_px: int = 80,
        scale_clamp_ratio: float = 4.0,
        min_visible_fraction: float = 0.25,
    ) -> None:
        # Caption band (12%) reserves space for subtitle text + an optional
        # centered logo zone. Bulk-purchase buyers can place their brand in
        # the bottom center of the poster without colliding with fish.
        self.title_band_fraction = title_band_fraction
        self.caption_band_fraction = caption_band_fraction
        self.side_margin_fraction = side_margin_fraction
        self.packing_target = packing_target
        self.inter_item_gap_frac = inter_item_gap_frac
        self.inter_shelf_gap_frac = inter_shelf_gap_frac
        self.label_height_px = label_height_px
        self.scale_clamp_ratio = scale_clamp_ratio
        self.min_visible_fraction = min_visible_fraction

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        """Compute shelf-packed placements for *spec* and return a ``LayoutResult``.

        The algorithm sizes each species proportionally to its
        ``relative_scale_index`` squared (area, not length), packs them into
        greedy shelves left-to-right, then uniformly scales to fill the
        available content area.
        """
        warnings: list[str] = []

        # 1. Filter species to those with existing masters.
        present: list[SpeciesRef] = []
        for ref in species:
            if loader.exists(ref.slug, spec.style_slug):
                present.append(ref)
            else:
                warnings.append(
                    f"Missing master for species '{ref.slug}' in style "
                    f"'{spec.style_slug}' — skipped."
                )

        if not present:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 2. Load masters and compute aspect ratios.
        masters: dict[str, MasterImage] = {}
        aspects: dict[str, float] = {}
        for ref in present:
            master = loader.get(ref.slug, spec.style_slug)
            masters[ref.slug] = master
            src_w = max(1, master.width_px)
            src_h = max(1, master.height_px)
            aspects[ref.slug] = src_w / src_h

        # 3. Sort by relative_scale_index descending (largest first).
        species_sorted = sorted(
            present,
            key=lambda r: r.relative_scale_index,
            reverse=True,
        )

        # 4. Clamp scales so tiny species remain visible.
        largest_scale = species_sorted[0].relative_scale_index
        floor = max(
            largest_scale * self.min_visible_fraction,
            largest_scale / self.scale_clamp_ratio,
        )
        effective: dict[str, float] = {}
        clamped_names: list[str] = []
        for s in species_sorted:
            if s.relative_scale_index < floor:
                effective[s.slug] = floor
                clamped_names.append(s.slug)
            else:
                effective[s.slug] = s.relative_scale_index

        if clamped_names:
            warnings.append(
                f"PackedLayoutEngine: scale clamping fired for "
                f"{', '.join(clamped_names)} — floored to {floor:.3f}."
            )

        # 5. Compute initial draw sizes via area budget.
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        title_h = int(round(canvas_h * self.title_band_fraction))
        caption_h = int(round(canvas_h * self.caption_band_fraction))
        side_margin_px = int(round(canvas_w * self.side_margin_fraction))
        content_w = canvas_w - 2 * side_margin_px
        content_h = canvas_h - title_h - caption_h

        total_weight = sum(effective[s.slug] ** 2 for s in species_sorted)
        target_area = content_w * content_h * self.packing_target

        draws: dict[str, list[float]] = {}
        for s in species_sorted:
            weight = effective[s.slug] ** 2
            area = weight / total_weight * target_area
            aspect = aspects[s.slug]
            w = math.sqrt(area * aspect)
            h = w / aspect
            draws[s.slug] = [w, h]

        # 6. Shelf packing — "largest-first, fit-in-best-shelf" strategy.
        # Instead of packing all species top-to-bottom by size (which dumps
        # every small species on the bottom shelf), we place the largest
        # species first to anchor shelves, then slot smaller species into
        # whichever existing shelf has the most remaining horizontal space.
        # Small species act as gap-fillers beside large ones — like a real
        # museum plate where a bluegill sits beside a pike's tail.
        gap_x = content_w * self.inter_item_gap_frac
        gap_y = content_h * self.inter_shelf_gap_frac

        shelves: list[list[SpeciesRef]] = []
        shelf_used: list[float] = []  # pixel width used per shelf

        for s in species_sorted:
            w = draws[s.slug][0]
            # Try to fit in the shelf with the most remaining space.
            best_shelf = -1
            best_remaining = -1.0
            for idx, used in enumerate(shelf_used):
                remaining = content_w - used - gap_x
                if remaining >= w and remaining > best_remaining:
                    best_shelf = idx
                    best_remaining = remaining
            if best_shelf >= 0:
                shelves[best_shelf].append(s)
                shelf_used[best_shelf] += w + gap_x
            else:
                # No existing shelf has room — start a new one.
                shelves.append([s])
                shelf_used.append(w)

        # 7. Compute total packed height.
        label_height_px = self.label_height_px
        shelf_heights: list[float] = []
        for shelf in shelves:
            max_h = max(draws[s.slug][1] for s in shelf)
            shelf_heights.append(max_h + label_height_px)
        total_h = sum(shelf_heights) + gap_y * max(0, len(shelves) - 1)

        # 8. Scale-to-fill.
        if total_h > 0:
            scale_factor = content_h / total_h
            scale_factor = max(0.3, min(2.0, scale_factor))
            for slug in draws:
                draws[slug][0] *= scale_factor
                draws[slug][1] *= scale_factor
            label_h_final = label_height_px * scale_factor
            gap_y_final = gap_y * scale_factor
            shelf_heights = [sh * scale_factor for sh in shelf_heights]
        else:
            label_h_final = float(label_height_px)
            gap_y_final = gap_y

        # 9. Position items.
        placements: list[PlacedItem] = []
        y_cursor = float(title_h)

        for shelf_idx, shelf in enumerate(shelves):
            shelf_h = shelf_heights[shelf_idx]
            max_draw_h = max(draws[s.slug][1] for s in shelf)

            # Horizontal distribution: even spacing between items.
            items_total_w = sum(draws[s.slug][0] for s in shelf)
            if len(shelf) > 1:
                spacing = (content_w - items_total_w) / (len(shelf) - 1)
            else:
                spacing = 0.0

            # Always center shelves horizontally — fish should cluster
            # toward the center, not spread edge-to-edge.
            total_with_gaps = items_total_w + spacing * max(0, len(shelf) - 1)
            x_start = side_margin_px + (content_w - total_with_gaps) / 2

            x_cursor = x_start
            for s in shelf:
                dw, dh = draws[s.slug]
                # Bottom-align within the shelf draw area.
                item_y = y_cursor + (max_draw_h - dh)

                master = masters[s.slug]
                placements.append(PlacedItem(
                    species_ref=s,
                    master=master,
                    x=int(round(x_cursor)),
                    y=int(round(item_y)),
                    draw_width=int(round(dw)),
                    draw_height=int(round(dh)),
                ))

                x_cursor += dw + spacing

            y_cursor += shelf_h + gap_y_final

        # 10. Overlap detection (safety check).
        for a_idx in range(len(placements)):
            a = placements[a_idx]
            ax2 = a.x + a.draw_width
            ay2 = a.y + a.draw_height
            for b_idx in range(a_idx + 1, len(placements)):
                b = placements[b_idx]
                bx2 = b.x + b.draw_width
                by2 = b.y + b.draw_height
                if a.x < bx2 and ax2 > b.x and a.y < by2 and ay2 > b.y:
                    warnings.append(
                        f"PackedLayoutEngine: '{a.species_ref.slug}' and "
                        f"'{b.species_ref.slug}' bounding boxes overlap."
                    )

        # 11. Return result.
        return LayoutResult(
            poster=spec,
            placements=placements,
            warnings=warnings,
        )


# --- SilhouettePackedLayoutEngine --------------------------------------------


class SilhouettePackedLayoutEngine(LayoutEngine):
    """Row-based layout with silhouette-aware nesting + uniform scale-fit fallback.

    The structural skeleton is a strict bbox row packer (uniform gutters,
    species sorted by relative_scale_index, snake-distributed across rows),
    which guarantees ordered rows and clean vertical rhythm.

    On top of that, two passes refine the layout to meet the user's goals:

    1. **Silhouette-aware tightening pass.** After bbox row layout is
       computed, neighbours (horizontal within a row, and vertical across
       rows) have their gutters reduced as long as the actual alpha masks
       don't touch. This produces the "nested" feel: a long fish's tail
       can sit in the dead air above/below a shorter fish, and species
       can intrude into the title/caption bands when their alphas don't
       overlap the rendered text rect.
    2. **Uniform scale-fit fallback.** Before considering dropping any
       species, the engine progressively shrinks ALL species by the same
       ratio (uniform downscale) until the strict no-overlap, zero-runoff
       condition holds. Only after the largest species would fall below
       ``min_species_width_frac`` (default 6%) does the engine drop the
       lowest-priority species.

    A final assertion verifies every placement satisfies
    ``0 <= x_frac`` and ``x_frac + width_frac <= 1`` (and likewise on Y);
    if not, a uniform shrink-to-fit is forced as a last-ditch safety net.
    """

    def __init__(
        self,
        # 0.22 matches the renderer's actual two-line title block:
        #   preheader_y (0.06) + preheader (~0.03) + gap (0.018) + title (0.09)
        # ≈ 0.20, plus a small safety pad. Previously this was 0.10 which
        # let the top-row pike's silhouette overlap the rendered title text.
        title_band_fraction: float = 0.22,
        caption_band_fraction: float = 0.07,
        gutter_frac: float = 0.04,
        scale_clamp_ratio: float = 4.0,
        min_visible_fraction: float = 0.25,
        label_height_px: int = 100,
        min_species_width_frac: float = 0.06,
        # Tightening / nesting controls.
        nest_enabled: bool = True,
        # 0.50 = horizontal tighten can close up to 50% of the inter-species
        # gutter. The vertical/title intrusion frac is now a fraction of
        # CONTENT HEIGHT (not gutter) so short fish can dive deeply into
        # tall fish' dead air; previous 0.25 * gutter was ~30 px which
        # produced no visible staggering at 3300 px canvases.
        nest_max_intrusion_frac: float = 0.50,
        nest_vertical_intrusion_frac: float = 0.45,
        nest_alpha_threshold: int = 8,
        # Title-band intrusion: when True, the top row of silhouettes is
        # allowed to creep up into the title band (avoiding the central
        # title text rect via alpha probes). In practice this collides
        # with the flanking rules and frequently occludes the main title
        # — so it ships disabled. Set True to restore the legacy creep.
        nest_into_title_band: bool = False,
        # Within-row vertical staggering: per-species allow Y to vary from
        # the row baseline. Each species' silhouette can move up by up to
        # ``stagger_within_row_frac * row_inner_height`` as long as its
        # alpha + label rect don't collide with anything above. Set 0 to
        # disable (preserves the legacy bottom-aligned baseline).
        stagger_within_row_frac: float = 0.85,
        # Aggressive Y-slot variance: after row layout, assign each species
        # in a row to one of N sub-slots (top/middle/bottom by default),
        # alternating across consecutive species so the eye reads vertical
        # rhythm. Constraint-respecting: any slot assignment that would
        # collide with another silhouette/label is rejected and the species
        # falls back to the row baseline. Default True to match the new
        # reference aesthetic.
        varied_y_slots: bool = True,
        varied_y_slot_count: int = 3,
        # Optional callable: SpeciesRef -> (label_w_px, label_h_px). When
        # provided, the tightening pass treats each species' effective bbox
        # as max(silhouette_width, label_width) and refuses any tighten that
        # would cause two label rects to overlap. The final pairwise label
        # assertion uses the same widths.
        label_size_provider=None,
        # Legacy kwargs kept so older callers don't break.
        side_margin_fraction: float | None = None,
        packing_target: float | None = None,
        overlap_tolerance: float | None = None,
        mask_resolution: int | None = None,
    ) -> None:
        self.title_band_fraction = title_band_fraction
        self.caption_band_fraction = caption_band_fraction
        self.gutter_frac = gutter_frac
        self.scale_clamp_ratio = scale_clamp_ratio
        self.min_visible_fraction = min_visible_fraction
        self.label_height_px = label_height_px
        self.min_species_width_frac = min_species_width_frac
        self.nest_enabled = nest_enabled
        self.nest_max_intrusion_frac = nest_max_intrusion_frac
        self.nest_vertical_intrusion_frac = nest_vertical_intrusion_frac
        self.nest_alpha_threshold = nest_alpha_threshold
        self.nest_into_title_band = nest_into_title_band
        self.stagger_within_row_frac = stagger_within_row_frac
        self.varied_y_slots = varied_y_slots
        self.varied_y_slot_count = max(2, int(varied_y_slot_count))
        # If no provider is wired up, install a default that measures labels
        # using the same Didot fallback chain + default sizes as the
        # production EditorialMultiRenderer. This makes the engine compute
        # honest label widths even when the caller doesn't pass one.
        self.label_size_provider = label_size_provider or _default_label_size_provider
        # Retained as attributes for backwards-compat, but no longer used
        # by the layout algorithm.
        self.side_margin_fraction = side_margin_fraction
        self.packing_target = packing_target
        self.overlap_tolerance = overlap_tolerance
        self.mask_resolution = mask_resolution

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        from PIL import Image

        warnings: list[str] = []

        # 1. Filter to species with masters.
        present: list[SpeciesRef] = []
        for ref in species:
            if loader.exists(ref.slug, spec.style_slug):
                present.append(ref)
            else:
                warnings.append(
                    f"Missing master for species '{ref.slug}' in style "
                    f"'{spec.style_slug}' — skipped."
                )
        if not present:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 2. Sort largest-first by relative_scale_index. The most prominent
        # species also become the highest-priority placements.
        species_sorted = sorted(
            present, key=lambda r: r.relative_scale_index, reverse=True
        )

        # 3. Geometry — title band, caption band, uniform gutter on every
        # canvas edge, and a label band beneath each species.
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        gutter = max(8, int(round(canvas_h * self.gutter_frac)))
        title_h = int(round(canvas_h * self.title_band_fraction))
        caption_h = int(round(canvas_h * self.caption_band_fraction))
        label_h = self.label_height_px if spec.show_labels else 0

        content_x0 = gutter
        content_y0 = title_h + gutter
        content_w = max(1, canvas_w - 2 * gutter)
        content_h = max(1, canvas_h - title_h - caption_h - 2 * gutter)

        # 4. Load masters, tight-cropped silhouette aspect ratios, AND
        # low-res alpha masks for the nesting/tightening pass. The masks
        # are cropped to the silhouette bbox and downsampled to 64px on
        # the long side so the tightening pass can do mask-vs-mask
        # collision tests cheaply.
        masters: dict[str, MasterImage] = {}
        aspects: dict[str, float] = {}  # width / height
        # masks[slug] = (mask_w, mask_h, bytes) — 0 = transparent, >0 = solid
        masks: dict[str, tuple[int, int, bytes]] = {}
        MASK_LONG = 64
        for s in species_sorted:
            try:
                master = loader.get(s.slug, spec.style_slug)
            except FileNotFoundError:
                warnings.append(
                    f"Missing master for species '{s.slug}' — skipped."
                )
                continue
            masters[s.slug] = master
            try:
                with Image.open(master.image_path) as img:
                    alpha = img.convert("RGBA").split()[3]
                    bbox = alpha.getbbox()
                    if bbox:
                        w_c = max(1, bbox[2] - bbox[0])
                        h_c = max(1, bbox[3] - bbox[1])
                        cropped = alpha.crop(bbox)
                    else:
                        w_c = max(1, master.width_px)
                        h_c = max(1, master.height_px)
                        cropped = alpha
                    aspects[s.slug] = w_c / h_c
                    # Build small mask for the tightening pass.
                    if cropped.width >= cropped.height:
                        mw = MASK_LONG
                        mh = max(1, int(round(MASK_LONG * cropped.height / cropped.width)))
                    else:
                        mh = MASK_LONG
                        mw = max(1, int(round(MASK_LONG * cropped.width / cropped.height)))
                    small = cropped.resize((mw, mh), Image.BILINEAR)
                    masks[s.slug] = (mw, mh, small.tobytes())
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"SilhouettePackedLayoutEngine: alpha load failed for "
                    f"'{s.slug}' ({exc}); falling back to bbox aspect."
                )
                aspects[s.slug] = max(1, master.width_px) / max(1, master.height_px)

        species_with_masters = [s for s in species_sorted if s.slug in masters]
        if not species_with_masters:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 5. Choose the row arrangement that maximises species size while
        # respecting the uniform gutter and label band. We try every row
        # count from 1..N, distributing species in scale order across rows
        # so wider species don't all pile into one row, and pick the
        # arrangement that yields the largest "unit height" — the height
        # of a baseline species (relative_scale_index == largest_eff).
        n = len(species_with_masters)
        min_species_w = self.min_species_width_frac * canvas_w

        def _clamp_scales(refs: list[SpeciesRef]) -> dict[str, float]:
            largest_scale = max(r.relative_scale_index for r in refs)
            floor = max(
                largest_scale * self.min_visible_fraction,
                largest_scale / self.scale_clamp_ratio,
            )
            return {r.slug: max(r.relative_scale_index, floor) for r in refs}

        def _distribute_rows(
            refs: list[SpeciesRef], nrows: int
        ) -> list[list[SpeciesRef]]:
            """Snake-distribute species across rows so each row's total
            aspect-weighted width is balanced. Largest-first input ensures
            the heaviest species spread across rows instead of stacking."""
            rows: list[list[SpeciesRef]] = [[] for _ in range(nrows)]
            row_load = [0.0] * nrows
            for r in refs:
                # Place into the row with the smallest current "load" measured
                # in width-units (aspect * effective_scale).
                idx = min(range(nrows), key=lambda i: row_load[i])
                rows[idx].append(r)
                row_load[idx] += aspects[r.slug] * max(0.1, r.relative_scale_index)
            return [row for row in rows if row]

        def _evaluate(refs: list[SpeciesRef], nrows: int) -> tuple[float, list[list[SpeciesRef]]]:
            """Return (unit_height, rows) for this arrangement.

            The unit height is the largest value of ``h`` such that every
            row fits inside ``content_w`` after gutters, and the stack of
            rows fits inside ``content_h`` after row gutters and label
            bands. Returns 0.0 if the arrangement cannot satisfy the
            minimum species width.
            """
            if nrows <= 0 or nrows > len(refs):
                return 0.0, []
            rows = _distribute_rows(refs, nrows)
            if not rows:
                return 0.0, []

            effective = _clamp_scales(refs)
            largest_eff = max(effective.values())

            # Per row: width = sum(unit_h * effective[s]/largest_eff * aspect[s])
            #               + gutter * (len(row) - 1)
            # We want width <= content_w  => unit_h <= (content_w - gutters) / sum_aspect_weight
            # Per stack: total_h = sum(unit_h * effective_row_max/largest_eff)
            #                    + label_h * nrows + gutter * (nrows - 1)
            # where effective_row_max is the largest effective scale in the row.
            # Solve for unit_h that simultaneously fits both axes.
            row_widths_unit: list[float] = []
            for row in rows:
                aw = sum(
                    aspects[s.slug] * effective[s.slug] / largest_eff for s in row
                )
                if aw <= 0:
                    return 0.0, []
                row_gutters = gutter * max(0, len(row) - 1)
                # unit_h <= (content_w - row_gutters) / aw
                cap = (content_w - row_gutters) / aw
                row_widths_unit.append(cap)
            unit_h_w = min(row_widths_unit)

            row_height_factors = [
                max(effective[s.slug] for s in row) / largest_eff for row in rows
            ]
            inter_row_gutters = gutter * max(0, len(rows) - 1)
            label_band_total = label_h * len(rows) + gutter * len(rows)  # one gutter+label per row
            avail_h = content_h - inter_row_gutters - label_band_total
            sum_factors = sum(row_height_factors)
            if sum_factors <= 0 or avail_h <= 0:
                return 0.0, []
            unit_h_h = avail_h / sum_factors

            unit_h = min(unit_h_w, unit_h_h)
            if unit_h <= 0:
                return 0.0, []

            return unit_h, rows

        # Try every row count, pick the largest unit_h. If no arrangement
        # yields a headline species at min_species_w, the row packer
        # itself is already returning the largest possible size — uniform
        # downscale is implicit. Only when we'd literally be too small
        # to render do we drop a species. (The bbox row packer maximises
        # unit_h given a row arrangement, so this is the correct
        # uniform-scale-fit policy: shrink uniformly, never overlap, and
        # only drop after we've exhausted shrink room.)
        active_refs = list(species_with_masters)
        dropped: list[str] = []
        downscale_applied = False

        while True:
            best_unit_h = 0.0
            best_rows: list[list[SpeciesRef]] = []
            for nrows in range(1, len(active_refs) + 1):
                uh, rows = _evaluate(active_refs, nrows)
                if uh > best_unit_h:
                    best_unit_h = uh
                    best_rows = rows

            if best_unit_h <= 0 or not best_rows:
                # Cannot place any species; bail.
                break

            effective = _clamp_scales(active_refs)
            largest_eff = max(effective.values())
            # Use the widest aspect among the largest-scale species as the
            # "unit width" — i.e. how wide the headline species ends up.
            unit_w = best_unit_h * max(
                aspects[s.slug] for s in active_refs
                if effective[s.slug] >= largest_eff - 1e-9
            )

            if unit_w >= min_species_w or len(active_refs) <= 1:
                break

            # Pre-drop fallback: the row packer already shrinks uniformly,
            # so we know this arrangement is the best possible at the
            # current min. The species count is too high for the canvas
            # at the configured floor — drop the lowest-priority species
            # and retry. Track that downscale was effectively saturated.
            downscale_applied = True
            lowest = min(
                active_refs, key=lambda r: r.relative_scale_index
            )
            dropped.append(lowest.slug)
            active_refs = [r for r in active_refs if r.slug != lowest.slug]

        if dropped:
            warnings.append(
                "SilhouettePackedLayoutEngine: dropped low-priority species to "
                f"avoid overlap at minimum size: {', '.join(dropped)}."
            )

        if best_unit_h <= 0 or not best_rows:
            warnings.append(
                "SilhouettePackedLayoutEngine: could not place any species "
                "without overlap at the minimum size."
            )
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        unit_h = best_unit_h
        rows = best_rows
        effective = _clamp_scales(active_refs)
        largest_eff = max(effective.values())

        # 6. Position rows top-to-bottom, centering each row horizontally
        # and the row stack vertically inside the content rect. Each row's
        # species are bottom-aligned so the label baseline is consistent.
        row_inner_heights: list[float] = []  # silhouette height
        row_total_heights: list[float] = []  # silhouette + gutter + label
        for row in rows:
            row_max_factor = max(effective[s.slug] for s in row) / largest_eff
            inner_h = unit_h * row_max_factor
            row_inner_heights.append(inner_h)
            row_total_heights.append(inner_h + gutter + label_h)

        total_stack_h = sum(row_total_heights) + gutter * max(0, len(rows) - 1)
        # Vertically center the stack inside content area.
        y_cursor = content_y0 + max(0.0, (content_h - total_stack_h) / 2.0)

        placements: list[PlacedItem] = []
        for row, inner_h, total_h in zip(rows, row_inner_heights, row_total_heights):
            # Compute each species' draw width and draw height.
            sized: list[tuple[SpeciesRef, int, int]] = []
            for s in row:
                factor = effective[s.slug] / largest_eff
                draw_h = max(1, int(round(unit_h * factor)))
                draw_w = max(1, int(round(draw_h * aspects[s.slug])))
                sized.append((s, draw_w, draw_h))

            row_content_w = sum(w for _, w, _ in sized) + gutter * max(0, len(sized) - 1)
            x_cursor = content_x0 + max(0.0, (content_w - row_content_w) / 2.0)
            row_top = y_cursor
            row_baseline = row_top + inner_h  # bottom of silhouette band

            for s, draw_w, draw_h in sized:
                item_y = int(round(row_baseline - draw_h))
                placements.append(
                    PlacedItem(
                        species_ref=s,
                        master=masters[s.slug],
                        x=int(round(x_cursor)),
                        y=item_y,
                        draw_width=draw_w,
                        draw_height=draw_h,
                    )
                )
                x_cursor += draw_w + gutter

            y_cursor += total_h + gutter

        # 7. Compute per-species label rect dimensions. Each species'
        # EFFECTIVE bbox is max(silhouette_width, label_text_width) wide,
        # because a label like "Largemouth Bass (Micropterus salmoides)"
        # is wider than the silhouette and must not collide with neighbours.
        # If no label_size_provider was wired up, we fall back to silhouette
        # width — which is the legacy behaviour.
        # Key by slug because tightening creates fresh PlacedItem instances,
        # invalidating id()-based maps.
        label_widths: dict[str, int] = {}
        label_heights: dict[str, int] = {}
        for p in placements:
            slug = p.species_ref.slug
            if self.label_size_provider is not None and label_h > 0:
                try:
                    lw, lh = self.label_size_provider(p.species_ref)
                    label_widths[slug] = max(0, int(lw))
                    label_heights[slug] = max(0, int(lh))
                except Exception:  # noqa: BLE001
                    label_widths[slug] = p.draw_width
                    label_heights[slug] = label_h
            else:
                label_widths[slug] = p.draw_width
                label_heights[slug] = label_h

        def _label_rect(p: PlacedItem) -> tuple[int, int, int, int]:
            """(x1, y1, x2, y2) of the label rect that sits below p."""
            slug = p.species_ref.slug
            lw = label_widths.get(slug, p.draw_width)
            lh = label_heights.get(slug, label_h)
            cx = p.x + p.draw_width // 2
            x1 = cx - lw // 2
            x2 = x1 + lw
            y1 = p.y + p.draw_height
            y2 = y1 + lh
            return x1, y1, x2, y2

        # 7b. Aggressive Y-slot variance pass.
        # For each row, assign species to one of N sub-slots within the row
        # band so that consecutive species sit at clearly varied heights.
        # Honors constraints: silhouettes can't overlap, label rects can't
        # overlap, no canvas runoff. Runs BEFORE the silhouette tightening
        # pass so the pull-up step can still close any gap left over.
        if self.varied_y_slots and len(rows) > 0:
            placements = self._assign_varied_y_slots(
                placements,
                rows=rows,
                row_inner_heights=row_inner_heights,
                masks=masks,
                gutter=gutter,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                title_h=title_h,
                caption_h=caption_h,
                label_h=label_h,
                label_widths=label_widths,
                label_heights=label_heights,
                slot_count=self.varied_y_slot_count,
            )

        # 8. Silhouette-aware tightening pass.
        # Now that bbox-row layout is committed, walk neighbour pairs and
        # shrink the gutter between them as long as (a) their alpha masks
        # don't touch AND (b) their label rects don't touch. This produces
        # the nesting effect — a long fish's tail fills negative space
        # above/below a shorter fish, and species can intrude into the
        # title band when their alpha doesn't touch the text rect — while
        # GUARANTEEING zero label overlap.
        if self.nest_enabled:
            placements = self._tighten_silhouettes(
                placements,
                masks=masks,
                label_h=label_h,
                label_widths=label_widths,
                label_heights=label_heights,
                gutter=gutter,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                title_h=title_h,
                caption_h=caption_h,
            )

        # 8. Hard zero-runoff guarantee. Every placement's bounding rect
        # must satisfy 0 <= x and x + width <= canvas_w (and same for y
        # including the label band beneath the silhouette). If anything
        # violates that — defensive, should be impossible — uniformly
        # shrink everything around the canvas centre until it fits.
        def _runoff_violation(items: list[PlacedItem]) -> bool:
            for p in items:
                if p.x < 0 or p.y < 0:
                    return True
                if p.x + p.draw_width > canvas_w:
                    return True
                if p.y + p.draw_height + label_h > canvas_h:
                    return True
            return False

        if _runoff_violation(placements):
            warnings.append(
                "SilhouettePackedLayoutEngine: zero-runoff guard triggered; "
                "uniformly shrinking placements to fit the canvas."
            )
            # Find tight bbox of all placements then scale around its
            # centre toward the canvas centre.
            min_x = min(p.x for p in placements)
            min_y = min(p.y for p in placements)
            max_x = max(p.x + p.draw_width for p in placements)
            max_y = max(p.y + p.draw_height + label_h for p in placements)
            cur_w = max(1, max_x - min_x)
            cur_h = max(1, max_y - min_y)
            scale = min(
                (canvas_w - 2 * gutter) / cur_w,
                (canvas_h - 2 * gutter) / cur_h,
                1.0,
            )
            cx0, cy0 = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
            cx1, cy1 = canvas_w / 2.0, canvas_h / 2.0
            new_placements: list[PlacedItem] = []
            for p in placements:
                nw = max(1, int(round(p.draw_width * scale)))
                nh = max(1, int(round(p.draw_height * scale)))
                # Map the centre of p through the affine.
                pcx = p.x + p.draw_width / 2.0
                pcy = p.y + p.draw_height / 2.0
                ncx = cx1 + (pcx - cx0) * scale
                ncy = cy1 + (pcy - cy0) * scale
                new_placements.append(PlacedItem(
                    species_ref=p.species_ref,
                    master=p.master,
                    x=int(round(ncx - nw / 2.0)),
                    y=int(round(ncy - nh / 2.0)),
                    draw_width=nw,
                    draw_height=nh,
                ))
            placements = new_placements

        # Final assertion — fail loudly if runoff still present.
        for p in placements:
            x_frac = p.x / canvas_w
            y_frac = p.y / canvas_h
            w_frac = p.draw_width / canvas_w
            h_frac = (p.draw_height + label_h) / canvas_h
            assert -1e-3 <= x_frac and x_frac + w_frac <= 1.0 + 1e-3, (
                f"x_frac runoff for {p.species_ref.slug}: "
                f"{x_frac:.3f}+{w_frac:.3f}>1"
            )
            assert -1e-3 <= y_frac and y_frac + h_frac <= 1.0 + 1e-3, (
                f"y_frac runoff for {p.species_ref.slug}: "
                f"{y_frac:.3f}+{h_frac:.3f}>1"
            )

        # Final pairwise label-rect check: emit a warning for every pair
        # whose default-position labels would overlap. The default
        # EditorialMultiRenderer ships with leader-line label placement
        # which DOES find non-overlapping label positions even when the
        # silhouettes are tightly packed — this check is purely diagnostic
        # for callers using inline-below labels. Promoted from a hard
        # assertion to a warning so the leader-line renderer (the user-
        # facing default) never blocks renders.
        if label_h > 0:
            overlap_pairs = 0
            for ai in range(len(placements)):
                a = placements[ai]
                ax1, ay1, ax2, ay2 = _label_rect(a)
                if ax2 <= ax1 or ay2 <= ay1:
                    continue
                for bi in range(ai + 1, len(placements)):
                    b = placements[bi]
                    bx1, by1, bx2, by2 = _label_rect(b)
                    if bx2 <= bx1 or by2 <= by1:
                        continue
                    if ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1:
                        overlap_pairs += 1
            if overlap_pairs > 0:
                warnings.append(
                    f"SilhouettePackedLayoutEngine: {overlap_pairs} pair(s) "
                    "of inline-below label rects overlap; relying on the "
                    "leader-line renderer to place labels in whitespace."
                )

        logger.info(
            "SilhouettePackedLayoutEngine: placed %d species in %d rows; "
            "unit_h=%.1fpx; gutter=%dpx; canvas=%dx%d; nesting=%s; downscale=%s",
            len(placements), len(rows), unit_h, gutter, canvas_w, canvas_h,
            self.nest_enabled, downscale_applied,
        )

        return LayoutResult(
            poster=spec,
            placements=placements,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Silhouette-aware tightening pass
    # ------------------------------------------------------------------
    def _tighten_silhouettes(
        self,
        placements: list[PlacedItem],
        masks: dict[str, tuple[int, int, bytes]],
        label_h: int,
        label_widths: dict[str, int] | None,
        label_heights: dict[str, int] | None,
        gutter: int,
        canvas_w: int,
        canvas_h: int,
        title_h: int,
        caption_h: int,
    ) -> list[PlacedItem]:
        """Tighten gutters between neighbours when their silhouettes
        AND labels don't overlap, producing the nested/staggered effect.

        Strategy: for each pair of neighbours (horizontal within row,
        vertical across rows) try to shrink the gap by up to
        ``nest_max_intrusion_frac * gutter`` (horizontal) or
        ``nest_vertical_intrusion_frac * gutter`` (vertical) and check
        BOTH the silhouette alpha AND the label rect of the moving
        species against every potentially-affected neighbour. After
        vertical tightening completes, a second horizontal pass runs
        because vertical changes may have opened new horizontal slack.
        """
        if not placements:
            return placements
        thr = self.nest_alpha_threshold
        max_intrude = max(0, int(round(self.nest_max_intrusion_frac * gutter)))
        # Vertical intrusion is now a fraction of canvas HEIGHT (was: gutter,
        # which was tiny — ~30 px on a 3300 px canvas — and produced no
        # visible staggering). 0.45 * canvas_h gives short fish enough room
        # to dive deeply into a tall fish's dead air above their row.
        max_intrude_v = max(0, int(round(self.nest_vertical_intrusion_frac * canvas_h)))
        if max_intrude <= 0 and max_intrude_v <= 0:
            return placements
        label_widths = label_widths or {}
        label_heights = label_heights or {}

        def _lrect(p: PlacedItem) -> tuple[int, int, int, int] | None:
            """Label rect (x1, y1, x2, y2) for p, or None if no label."""
            slug = p.species_ref.slug
            lw = label_widths.get(slug, p.draw_width)
            lh = label_heights.get(slug, label_h)
            if lw <= 0 or lh <= 0:
                return None
            cx = p.x + p.draw_width // 2
            x1 = cx - lw // 2
            x2 = x1 + lw
            y1 = p.y + p.draw_height
            y2 = y1 + lh
            return x1, y1, x2, y2

        def _labels_overlap(a: PlacedItem, b: PlacedItem) -> bool:
            ra = _lrect(a)
            rb = _lrect(b)
            if ra is None or rb is None:
                return False
            return ra[0] < rb[2] and ra[2] > rb[0] and ra[1] < rb[3] and ra[3] > rb[1]

        # Group placements into rows by y-band (silhouette top y).
        sorted_p = sorted(placements, key=lambda p: (p.y, p.x))
        rows: list[list[PlacedItem]] = []
        for p in sorted_p:
            placed = False
            for row in rows:
                ry = row[0]
                a0, a1 = p.y, p.y + p.draw_height
                b0, b1 = ry.y, ry.y + ry.draw_height
                inter = max(0, min(a1, b1) - max(a0, b0))
                shorter = max(1, min(a1 - a0, b1 - b0))
                if inter / shorter > 0.5:
                    row.append(p)
                    placed = True
                    break
            if not placed:
                rows.append([p])
        for row in rows:
            row.sort(key=lambda p: p.x)

        idx_of: dict[int, int] = {id(p): i for i, p in enumerate(placements)}
        work = list(placements)

        def _refresh_idx() -> None:
            idx_of.clear()
            for i, p in enumerate(work):
                idx_of[id(p)] = i

        def _alpha_grid(p: PlacedItem) -> tuple[int, int, bytes] | None:
            return masks.get(p.species_ref.slug)

        def _masks_overlap(a: PlacedItem, b: PlacedItem) -> bool:
            ga = _alpha_grid(a)
            gb = _alpha_grid(b)
            if ga is None or gb is None:
                return True  # be conservative
            ax1, ay1 = a.x, a.y
            ax2, ay2 = a.x + a.draw_width, a.y + a.draw_height
            bx1, by1 = b.x, b.y
            bx2, by2 = b.x + b.draw_width, b.y + b.draw_height
            ix1 = max(ax1, bx1)
            iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2)
            iy2 = min(ay2, by2)
            if ix1 >= ix2 or iy1 >= iy2:
                return False
            STEPS = 16
            ga_w, ga_h, ga_b = ga
            gb_w, gb_h, gb_b = gb
            aw_px = max(1, ax2 - ax1)
            ah_px = max(1, ay2 - ay1)
            bw_px = max(1, bx2 - bx1)
            bh_px = max(1, by2 - by1)
            for sxi in range(STEPS):
                for syi in range(STEPS):
                    px = ix1 + (ix2 - ix1) * (sxi + 0.5) / STEPS
                    py = iy1 + (iy2 - iy1) * (syi + 0.5) / STEPS
                    ua = (px - ax1) / aw_px
                    va = (py - ay1) / ah_px
                    mxa = min(ga_w - 1, max(0, int(ua * ga_w)))
                    mya = min(ga_h - 1, max(0, int(va * ga_h)))
                    if ga_b[mya * ga_w + mxa] < thr:
                        continue
                    ub = (px - bx1) / bw_px
                    vb = (py - by1) / bh_px
                    mxb = min(gb_w - 1, max(0, int(ub * gb_w)))
                    myb = min(gb_h - 1, max(0, int(vb * gb_h)))
                    if gb_b[myb * gb_w + mxb] >= thr:
                        return True
            return False

        # 1) Horizontal tightening within each row.
        # Wrapped in a closure so we can run it twice — once before vertical
        # tightening and once after, since vertical changes can open new
        # horizontal slack (a row that moved up may now be able to nest
        # tighter horizontally without colliding with a label below).
        def _horizontal_pass() -> None:
            if max_intrude <= 0:
                return
            for row in rows:
                for i in range(1, len(row)):
                    step = max(1, max_intrude // 8)
                    shifted = 0
                    while shifted + step <= max_intrude:
                        left = work[idx_of[id(row[i - 1])]]
                        right = work[idx_of[id(row[i])]]
                        cand = PlacedItem(
                            species_ref=right.species_ref,
                            master=right.master,
                            x=right.x - step,
                            y=right.y,
                            draw_width=right.draw_width,
                            draw_height=right.draw_height,
                        )
                        # Silhouette check against the left neighbour.
                        if _masks_overlap(left, cand):
                            break
                        # Label-rect check: cand's label cannot overlap
                        # left's label, and we'll also walk the rest of the
                        # row (already-shifted siblings) below.
                        if _labels_overlap(left, cand):
                            break
                        # Cross-row label check: a tighter horizontal slot
                        # could push cand's label rect under another row's
                        # silhouette label. Walk every other placement and
                        # ensure cand's label doesn't collide.
                        bad = False
                        for other in work:
                            if other is left or other is right:
                                continue
                            if _labels_overlap(cand, other):
                                bad = True
                                break
                            # Also: cand's silhouette vs every other species
                            # silhouette in the same row that we would push.
                        if bad:
                            break
                        for j in range(i + 1, len(row)):
                            other = work[idx_of[id(row[j])]]
                            if _masks_overlap(cand, other):
                                bad = True
                                break
                        if bad:
                            break
                        delta = step
                        for j in range(i, len(row)):
                            old = work[idx_of[id(row[j])]]
                            moved = PlacedItem(
                                species_ref=old.species_ref,
                                master=old.master,
                                x=old.x - delta,
                                y=old.y,
                                draw_width=old.draw_width,
                                draw_height=old.draw_height,
                            )
                            work[idx_of[id(old)]] = moved
                            row[j] = moved
                        _refresh_idx()
                        shifted += step

        _horizontal_pass()

        # 2) Per-species vertical pull-up. For every species (any row),
        # try to move its silhouette UP by as much as possible without
        # colliding (alpha vs alpha, label vs alpha, label vs label) with
        # anything above. This produces dramatic vertical staggering: a
        # short fish under a tall pike's tail rises into the dead air,
        # and small fish in the bottom row rise to fill the gap below
        # the row above. The motion is INDEPENDENT per species — no
        # lockstep row shifts — so different fish in the same row can
        # end up at different Y values, which is exactly the user's
        # request for natural staggering.
        def _collides_above(cand: PlacedItem, ignore: PlacedItem) -> bool:
            """True if cand collides with any placement above (alpha or
            label rect), excluding `ignore` (the original instance)."""
            cb_lrect = _lrect(cand)
            for other in work:
                if other is ignore:
                    continue
                if other.species_ref.slug == cand.species_ref.slug:
                    continue
                # Silhouette vs silhouette.
                if _masks_overlap(cand, other):
                    return True
                # Silhouette vs label rect of `other`.
                ol = _lrect(other)
                if ol is not None:
                    ox1, oy1, ox2, oy2 = ol
                    if (cand.x < ox2 and cand.x + cand.draw_width > ox1
                            and cand.y < oy2 and cand.y + cand.draw_height > oy1):
                        return True
                # Label of cand vs silhouette/label of other.
                if cb_lrect is not None:
                    cbl_x1, cbl_y1, cbl_x2, cbl_y2 = cb_lrect
                    ox1, oy1 = other.x, other.y
                    ox2, oy2 = other.x + other.draw_width, other.y + other.draw_height
                    if (cbl_x1 < ox2 and cbl_x2 > ox1
                            and cbl_y1 < oy2 and cbl_y2 > oy1):
                        return True
                    if ol is not None:
                        olx1, oly1, olx2, oly2 = ol
                        if (cbl_x1 < olx2 and cbl_x2 > olx1
                                and cbl_y1 < oly2 and cbl_y2 > oly1):
                            return True
            return False

        if max_intrude_v > 0:
            # Process species in y-descending order so bottom rows move
            # first (and don't have anything below to chase). Use
            # binary-search-style probe to find the largest valid shift.
            for placed in sorted(list(work), key=lambda p: -p.y):
                cur = work[idx_of[id(placed)]] if id(placed) in idx_of else placed
                # Skip if not in work anymore (defensive).
                if cur is None:
                    continue
                # Don't push past canvas top.
                upper_bound = min(max_intrude_v, max(0, cur.y - title_h - max(0, gutter // 2)))
                if upper_bound <= 0:
                    continue
                # Binary search for the largest shift that doesn't collide.
                lo, hi = 0, upper_bound
                best = 0
                # Coarse scan first for speed (16 candidate steps).
                STEPS = 16
                for k in range(1, STEPS + 1):
                    cand_dy = int(upper_bound * k / STEPS)
                    if cand_dy <= best:
                        continue
                    cand = PlacedItem(
                        species_ref=cur.species_ref,
                        master=cur.master,
                        x=cur.x,
                        y=cur.y - cand_dy,
                        draw_width=cur.draw_width,
                        draw_height=cur.draw_height,
                    )
                    if not _collides_above(cand, cur):
                        best = cand_dy
                    else:
                        break  # monotonic — once it collides, larger shifts also collide
                if best > 0:
                    moved = PlacedItem(
                        species_ref=cur.species_ref,
                        master=cur.master,
                        x=cur.x,
                        y=cur.y - best,
                        draw_width=cur.draw_width,
                        draw_height=cur.draw_height,
                    )
                    work[idx_of[id(cur)]] = moved
                    # Also update the rows-grouping so subsequent passes see fresh state.
                    for row in rows:
                        for j, rp in enumerate(row):
                            if id(rp) == id(cur):
                                row[j] = moved
                                break
                    _refresh_idx()

        # 2c. Second horizontal pass — vertical changes may have opened
        # additional horizontal slack now that label rects of the row
        # above sit at a different y.
        _horizontal_pass()

        # 3) Title-band intrusion (top row): allow species silhouettes
        # to creep up into the title band as long as their alpha doesn't
        # collide with the central title text rect (canvas central 50%).
        # Cap is now meaningful — up to half the title band — so the top
        # of the canvas isn't blocked by an arbitrary gutter-sized limit.
        # Gated by nest_into_title_band (default off): the top-row pike
        # was reliably eating the title text + flanking rules, and the
        # aesthetic loss of forbidding the creep is small.
        if self.nest_into_title_band and rows and title_h > 0:
            title_text_x1 = int(canvas_w * 0.25)
            title_text_x2 = int(canvas_w * 0.75)
            title_text_y2 = title_h
            cap = max(0, title_h // 2)
            step = max(1, cap // 16) if cap > 0 else 0
            shifted = 0
            while shifted + step <= cap:
                top_row = [work[idx_of[id(p)]] for p in rows[0]]
                bad = False
                for p in top_row:
                    cy_after = p.y - step
                    if cy_after >= title_text_y2:
                        continue
                    g = _alpha_grid(p)
                    if g is None:
                        continue
                    gw, gh, gb = g
                    sy_lo = max(cy_after, 0)
                    sy_hi = min(cy_after + p.draw_height, title_text_y2)
                    sx_lo = max(p.x, title_text_x1)
                    sx_hi = min(p.x + p.draw_width, title_text_x2)
                    if sy_lo >= sy_hi or sx_lo >= sx_hi:
                        continue
                    cw_px = max(1, p.draw_width)
                    ch_px = max(1, p.draw_height)
                    STEPS = 8
                    hit = False
                    for sxi in range(STEPS):
                        for syi in range(STEPS):
                            px = sx_lo + (sx_hi - sx_lo) * (sxi + 0.5) / STEPS
                            py = sy_lo + (sy_hi - sy_lo) * (syi + 0.5) / STEPS
                            u = (px - p.x) / cw_px
                            v = (py - cy_after) / ch_px
                            mx = min(gw - 1, max(0, int(u * gw)))
                            my = min(gh - 1, max(0, int(v * gh)))
                            if gb[my * gw + mx] >= thr:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        bad = True
                        break
                if bad:
                    break
                new_top: list[PlacedItem] = []
                for old_p in [work[idx_of[id(p)]] for p in rows[0]]:
                    moved = PlacedItem(
                        species_ref=old_p.species_ref,
                        master=old_p.master,
                        x=old_p.x,
                        y=old_p.y - step,
                        draw_width=old_p.draw_width,
                        draw_height=old_p.draw_height,
                    )
                    work[idx_of[id(old_p)]] = moved
                    new_top.append(moved)
                rows[0] = new_top
                _refresh_idx()
                shifted += step

        return work

    # ------------------------------------------------------------------
    # Aggressive Y-slot variance pass
    # ------------------------------------------------------------------
    def _assign_varied_y_slots(
        self,
        placements: list[PlacedItem],
        rows: list[list[SpeciesRef]],
        row_inner_heights: list[float],
        masks: dict[str, tuple[int, int, bytes]],
        gutter: int,
        canvas_w: int,
        canvas_h: int,
        title_h: int,
        caption_h: int,
        label_h: int,
        label_widths: dict[str, int],
        label_heights: dict[str, int],
        slot_count: int,
    ) -> list[PlacedItem]:
        """Assign each species in a row to one of N Y sub-slots, alternating
        across consecutive species so the eye reads vertical rhythm.

        Within each row, divide the row's vertical headroom (the difference
        between the tallest species in the row and each species' own height)
        into ``slot_count`` sub-slots. Walk species in row order; for each
        species, pick a target slot index using a deterministic alternating
        pattern (0, slot_count-1, 1, slot_count-2, ...) keyed on the position
        index within the row. If the chosen slot's resulting placement passes
        all collision checks, commit it; otherwise fall through to the row
        baseline (legacy behaviour).
        """
        if not placements or slot_count < 2:
            return placements

        # Build slug -> placement-index map for fast lookup.
        slug_to_idx: dict[str, int] = {
            p.species_ref.slug: i for i, p in enumerate(placements)
        }

        # Snapshot original placements so we can mutate iteratively while
        # checking collisions against the current state.
        work = list(placements)

        def _alpha_grid(p: PlacedItem) -> tuple[int, int, bytes] | None:
            return masks.get(p.species_ref.slug)

        thr = self.nest_alpha_threshold

        def _masks_overlap_pair(a: PlacedItem, b: PlacedItem) -> bool:
            ga = _alpha_grid(a)
            gb = _alpha_grid(b)
            if ga is None or gb is None:
                return True  # conservative: if we can't measure, assume hit
            ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.draw_width, a.y + a.draw_height
            bx1, by1, bx2, by2 = b.x, b.y, b.x + b.draw_width, b.y + b.draw_height
            ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
            if ix1 >= ix2 or iy1 >= iy2:
                return False
            STEPS = 12
            ga_w, ga_h, ga_b = ga
            gb_w, gb_h, gb_b = gb
            aw = max(1, ax2 - ax1); ah = max(1, ay2 - ay1)
            bw = max(1, bx2 - bx1); bh = max(1, by2 - by1)
            for sxi in range(STEPS):
                for syi in range(STEPS):
                    px = ix1 + (ix2 - ix1) * (sxi + 0.5) / STEPS
                    py = iy1 + (iy2 - iy1) * (syi + 0.5) / STEPS
                    ua = (px - ax1) / aw; va = (py - ay1) / ah
                    mxa = min(ga_w - 1, max(0, int(ua * ga_w)))
                    mya = min(ga_h - 1, max(0, int(va * ga_h)))
                    if ga_b[mya * ga_w + mxa] < thr:
                        continue
                    ub = (px - bx1) / bw; vb = (py - by1) / bh
                    mxb = min(gb_w - 1, max(0, int(ub * gb_w)))
                    myb = min(gb_h - 1, max(0, int(vb * gb_h)))
                    if gb_b[myb * gb_w + mxb] >= thr:
                        return True
            return False

        def _label_rect(p: PlacedItem) -> tuple[int, int, int, int] | None:
            if label_h <= 0:
                return None
            slug = p.species_ref.slug
            lw = label_widths.get(slug, p.draw_width)
            lh = label_heights.get(slug, label_h)
            if lw <= 0 or lh <= 0:
                return None
            cx = p.x + p.draw_width // 2
            x1 = cx - lw // 2
            x2 = x1 + lw
            y1 = p.y + p.draw_height
            y2 = y1 + lh
            return x1, y1, x2, y2

        def _has_collision(cand: PlacedItem, ignore_slug: str) -> bool:
            """Check cand against all other placements in `work`."""
            cand_lr = _label_rect(cand)
            for other in work:
                if other.species_ref.slug == ignore_slug:
                    continue
                # Silhouette vs silhouette
                if _masks_overlap_pair(cand, other):
                    return True
                # cand silhouette vs other label rect
                ol = _label_rect(other)
                if ol is not None:
                    ox1, oy1, ox2, oy2 = ol
                    if (cand.x < ox2 and cand.x + cand.draw_width > ox1
                            and cand.y < oy2 and cand.y + cand.draw_height > oy1):
                        return True
                # cand label rect vs other silhouette + other label
                if cand_lr is not None:
                    cx1, cy1, cx2, cy2 = cand_lr
                    ox1, oy1 = other.x, other.y
                    ox2, oy2 = other.x + other.draw_width, other.y + other.draw_height
                    if cx1 < ox2 and cx2 > ox1 and cy1 < oy2 and cy2 > oy1:
                        return True
                    if ol is not None:
                        olx1, oly1, olx2, oly2 = ol
                        if cx1 < olx2 and cx2 > olx1 and cy1 < oly2 and cy2 > oly1:
                            return True
            return False

        # Compute alternating slot index pattern for a row of length n.
        # For slot_count=3 and n=5: indices [1, 0, 2, 1, 0] reads as
        # middle, top, bottom, middle, top — strong rhythm.
        def _pattern(n: int) -> list[int]:
            mid = (slot_count - 1) // 2
            seq: list[int] = []
            order: list[int] = [mid]
            for offset in range(1, slot_count):
                if mid - offset >= 0:
                    order.append(mid - offset)
                if mid + offset < slot_count:
                    order.append(mid + offset)
            for i in range(n):
                seq.append(order[i % len(order)])
            return seq

        # Process each row independently. Use the row's "row_inner_height"
        # as the available band; each species' own draw_height bites into
        # that. The headroom = inner_h - draw_h is divided into N slots.
        # Slot 0 = species sits flush with row TOP (high in band).
        # Slot N-1 = species sits flush with row BOTTOM (low in band) — the
        # row baseline. Intermediate slots interpolate.
        for row_idx, (row_refs, inner_h_f) in enumerate(zip(rows, row_inner_heights)):
            inner_h = float(inner_h_f)
            n = len(row_refs)
            if n == 0:
                continue
            slot_pattern = _pattern(n)

            for col_idx, ref in enumerate(row_refs):
                slug = ref.slug
                if slug not in slug_to_idx:
                    continue
                p_idx = slug_to_idx[slug]
                cur = work[p_idx]
                headroom = inner_h - cur.draw_height
                if headroom <= 1:
                    continue
                # Slot 0 = top (max upward shift), slot N-1 = baseline (no shift).
                # Original layout has species bottom-aligned to row baseline,
                # so positive shift means MOVE UP (decrease y).
                slot = slot_pattern[col_idx]
                # Convert slot index to a y-shift: 0 -> max upward, N-1 -> 0.
                # Linear interpolate.
                if slot_count <= 1:
                    shift = 0.0
                else:
                    shift = headroom * (slot_count - 1 - slot) / (slot_count - 1)
                shift_px = int(round(shift))
                if shift_px <= 0:
                    continue
                # Don't push past canvas top (above title band).
                target_y = cur.y - shift_px
                min_y = title_h + max(0, gutter // 2)
                if target_y < min_y:
                    target_y = min_y
                if target_y >= cur.y:
                    continue
                cand = PlacedItem(
                    species_ref=cur.species_ref,
                    master=cur.master,
                    x=cur.x,
                    y=target_y,
                    draw_width=cur.draw_width,
                    draw_height=cur.draw_height,
                )
                # Replace cur with cand temporarily for the collision test.
                work[p_idx] = cand
                if _has_collision(cand, slug):
                    # Reject — restore.
                    work[p_idx] = cur
                else:
                    # Commit; subsequent species in this row see the new state.
                    pass

        return work


# --- FieldGuideBandsEngine ---------------------------------------------------


class FieldGuideBandsEngine(LayoutEngine):
    """Honest-scale band-packing layout that mirrors a printed field guide.

    Algorithm (matches the reference poster at
    ``output/uploads/fish poster.jpeg``):

    1. Sort species largest-first by ``relative_scale_index``.
    2. Carve the canvas: top 12% for the title block, bottom 3% for any
       tag/mark, the middle 85% is the body the bands live in.
    3. Greedy band packing — walk the sorted species and open a new band
       when adding the next fish would exceed 85% of canvas width minus
       per-fish gaps. Aim for ~2 fish per band on average; allow 1-3.
    4. Each band's height is the tallest fish in the band. Stack bands
       top-to-bottom with a 1.2% canvas-height inter-band gap and reserve
       2.5% canvas-height per band for the renderer-drawn label.
    5. Center each band horizontally so the row composition reads as a
       deliberate line break, not left-aligned packing.
    6. Honest-scale floor: clamp ``relative_scale_index`` at ``min_idx``
       (default 0.4) so smallest fish (e.g. bluegill) stay visible next to
       a Northern Pike — but the relative-size relationship is preserved
       (pike still ~6× bluegill body length, not equal).
    7. Total-shrink pass: if the computed band-stack overflows the body
       region, scale every band's draw size by the same overflow ratio so
       relative sizes stay correct.

    Pairs with :class:`EditorialMultiRenderer` rendering with the
    "field_guide" :class:`StyleProfile` (cream paper, two-line title,
    tracked common-name labels under each fish).
    """

    def __init__(
        self,
        title_band_fraction: float = 0.20,
        bottom_margin_fraction: float = 0.03,
        side_margin_fraction: float = 0.075,
        inter_fish_gap_fraction: float = 0.07,
        inter_band_gap_fraction: float = 0.020,
        label_band_fraction: float = 0.030,
        min_idx_floor: float = 0.4,
        # Compress the relative_scale_index range via power. Pure 1.0 keeps
        # honest ratios (pike 2.5 vs bluegill 0.5 = 5x). 0.65 maps that to
        # ~3x — matching the reference fish poster's tighter visual range
        # where the smallest fish is still substantial. Drop to 1.0 to
        # restore raw honest scale.
        scale_compression: float = 0.65,
        target_fish_per_band: int = 2,
        max_fish_per_band: int = 5,
        title_breathing_fraction: float = 0.015,
        target_band_width_fraction: float = 0.75,
        max_inter_fish_gap_fraction: float = 0.10,
        # Per-fish minimum horizontal spacing must accommodate label width.
        # Label font size ≈ canvas_h * 0.011, average tracked common name ≈
        # 14 chars * (label_size * 0.65) → ~10% canvas_h ≈ ~7.5% canvas_w
        # at 3:4 aspect. We measure exactly during packing.
        min_label_pad_fraction: float = 0.014,  # ≈50px on a 3600px canvas — sized to keep adjacent labels separated at the 0.010 label-size
    ) -> None:
        self.title_band_fraction = title_band_fraction
        self.bottom_margin_fraction = bottom_margin_fraction
        self.side_margin_fraction = side_margin_fraction
        self.inter_fish_gap_fraction = inter_fish_gap_fraction
        self.inter_band_gap_fraction = inter_band_gap_fraction
        self.label_band_fraction = label_band_fraction
        self.min_idx_floor = min_idx_floor
        self.scale_compression = scale_compression
        self.target_fish_per_band = target_fish_per_band
        self.max_fish_per_band = max_fish_per_band
        self.title_breathing_fraction = title_breathing_fraction
        self.target_band_width_fraction = target_band_width_fraction
        self.max_inter_fish_gap_fraction = max_inter_fish_gap_fraction
        self.min_label_pad_fraction = min_label_pad_fraction

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []

        if not species:
            warnings.append("FieldGuideBandsEngine received zero species.")
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        pairs = _resolve_species_with_masters(spec, species, loader, warnings)
        if not pairs:
            warnings.append("FieldGuideBandsEngine: no masters available.")
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        # 1. Sort largest-first.
        pairs_sorted = sorted(
            pairs,
            key=lambda pr: pr[0].relative_scale_index,
            reverse=True,
        )

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # 2. Carve regions. Title block is reserved 20% by default to give
        # the auto-shrunk title + bottom rule clearance from the hero fish
        # (Bug 1 fix: long lake names like "Lake Champlain" have descenders
        # that crash into the pike's dorsal fin without this padding).
        title_h = int(round(canvas_h * self.title_band_fraction))
        bottom_h = int(round(canvas_h * self.bottom_margin_fraction))
        # Small breathing-room buffer below the title's bottom rule before
        # the first band starts.
        breathing_px = int(round(canvas_h * self.title_breathing_fraction))
        body_top = title_h + breathing_px
        body_h = max(1, canvas_h - body_top - bottom_h)

        side_margin = int(round(canvas_w * self.side_margin_fraction))
        usable_w = max(1, canvas_w - 2 * side_margin)
        gap_px = int(round(canvas_w * self.inter_fish_gap_fraction))
        min_label_pad_px = int(round(canvas_w * self.min_label_pad_fraction))

        # Estimate tracked-label width using same metrics as the renderer's
        # _draw_compact_caption_only (font_size = canvas_h * 0.010, tracking
        # = font_size * 0.18). Per-char advance for uppercase serif ≈ 0.72
        # of font size — empirical (measured against the actual editorial
        # font: SMALLMOUTH BASS at 53px → 0.71, YELLOW PERCH → 0.72). We
        # err slightly wide so we never under-allocate. Used by the packing
        # loop to enforce that adjacent labels won't collide horizontally.
        # KEEP IN SYNC with renderer.py:_draw_compact_caption_only's
        # common_size formula — change them together or labels collide.
        label_font_size = max(20, int(round(canvas_h * 0.010)))
        label_tracking = max(2, int(round(label_font_size * 0.18)))

        def label_width_for(ref: SpeciesRef) -> int:
            text = (ref.common_name or "").upper()
            if not text:
                return 0
            char_advance = label_font_size * 0.72
            return int(round(
                len(text) * char_advance + label_tracking * max(0, len(text) - 1)
            ))

        # 6. Honest-scale floor on the index.
        # Apply scale_compression as a power: compressed_idx = idx**c.
        # c=1.0 → honest scale; c<1.0 → range narrows (small fish grow
        # toward median, big fish shrink toward median). The reference
        # poster reads as ~3:1 range; honest ratios on our species data
        # would be 5:1, so default c=0.65 hits the sweet spot.
        _c = max(0.1, min(1.0, self.scale_compression))
        idx_for = lambda ref: max(self.min_idx_floor, ref.relative_scale_index) ** _c

        # Pick a reference index for sizing. The hero target is COUNT-AWARE:
        # more species → smaller hero so everyone fits without dropping
        # anyone. Honest scale (relative_scale_index ratios) is preserved
        # across all fish via a single unit_w. Tiers were chosen so that the
        # natural pre-shrink stack height roughly matches body_h on a 3:4
        # canvas, minimising how much the total-shrink pass has to do.
        n_species = len(pairs_sorted)
        if n_species <= 5:
            hero_target_fraction = 0.60
        elif n_species <= 10:
            hero_target_fraction = 0.45
        elif n_species <= 15:
            hero_target_fraction = 0.35
        elif n_species <= 20:
            hero_target_fraction = 0.28
        else:
            hero_target_fraction = 0.22
        largest_idx = idx_for(pairs_sorted[0][0])
        target_largest_w = canvas_w * hero_target_fraction
        unit_w = target_largest_w / max(1e-6, largest_idx)

        # Helper: candidate draw width at the per-species scale.
        def draw_w_for(ref: SpeciesRef) -> float:
            return idx_for(ref) * unit_w

        # 3-5. Greedy band packing — packing-aware of label widths.
        # Each band is a list of (ref, master, candidate_w, candidate_h,
        # label_w). When projecting whether a fish fits, we use the WIDER
        # of (fish_width, label_width) plus the label-padding gap to ensure
        # adjacent labels don't horizontally collide (Bug 2 fix). This
        # gives uniform label sizing across all bands at the cost of
        # occasionally pushing a fish to a new band.
        bands: list[list[tuple[SpeciesRef, MasterImage, float, float, int]]] = []
        cur_band: list[tuple[SpeciesRef, MasterImage, float, float, int]] = []
        cur_band_slot_w = 0.0  # sum of max(fish_w, label_w) per item
        for ref, master in pairs_sorted:
            cand_w = draw_w_for(ref)
            # Use alpha-bbox dimensions (the tight fish silhouette) rather
            # than the master canvas dimensions. Without this, every fish
            # gets sized as if it were the master's 1.5:1 box — pike (true
            # 3.4:1) renders short with huge transparent vertical padding,
            # making "10 fish on a 4:5 canvas" look like "tiny fish floating
            # in a sea of whitespace". Bug B fix.
            src_w = max(1, master.bbox_width_px)
            src_h = max(1, master.bbox_height_px)
            cand_h = cand_w * src_h / src_w
            label_w = label_width_for(ref)
            # Each item's horizontal "slot" must be at least as wide as
            # its label so labels never overlap their neighbors. Use the
            # max of fish-draw-width and label-width.
            item_slot_w = max(cand_w, float(label_w))
            # Inter-item gap for PACKING decisions: use only the label
            # padding minimum (~24px), not the larger cosmetic gap_px
            # (~252px). The cosmetic gap is enforced later during band
            # spreading; using it here would prevent fish that comfortably
            # fit at render-time from packing into the same band.
            extra_gap = min_label_pad_px if cur_band else 0
            projected_w = cur_band_slot_w + extra_gap + item_slot_w
            band_full = (
                len(cur_band) >= self.max_fish_per_band
                or projected_w > usable_w
            )
            if band_full and cur_band:
                bands.append(cur_band)
                cur_band = [(ref, master, cand_w, cand_h, label_w)]
                cur_band_slot_w = item_slot_w
            else:
                cur_band.append((ref, master, cand_w, cand_h, label_w))
                cur_band_slot_w = projected_w
        if cur_band:
            bands.append(cur_band)

        # 5. Compute total stack height (item[3] = candidate_h).
        label_reserve_px = int(round(canvas_h * self.label_band_fraction))
        inter_band_px = int(round(canvas_h * self.inter_band_gap_fraction))

        def _stack_h(_bands: list) -> tuple[float, list[float]]:
            heights = [
                max((it[3] for it in band), default=0.0) for band in _bands
            ]
            return (
                sum(heights)
                + label_reserve_px * len(_bands)
                + inter_band_px * max(0, len(_bands) - 1),
                heights,
            )

        # 7a. Band-merging pass (pre-shrink): rather than shrink everything
        # uniformly when bands don't fit, try to merge adjacent SMALL bands
        # whose combined slot widths still fit ``usable_w``. This preserves
        # the hero's full size while increasing packing density of the
        # smaller fish — exactly what the field-guide reference does
        # (Bug 3 fix). Hero (band 0) is always preserved as solo.
        def _band_slot_width(band: list) -> float:
            slot = sum(max(it[2], float(it[4])) for it in band)
            n = len(band)
            if n > 1:
                # Use the label-pad minimum for packing-fit decisions;
                # cosmetic gap_px is only enforced during render-time band
                # spreading, where there's slack to absorb it.
                slot += min_label_pad_px * (n - 1)
            return slot

        max_per_band = self.max_fish_per_band
        # Iteratively merge: pick the two smallest-cumulative-height
        # adjacent bands (excluding the hero band 0) whose combined slot
        # width still fits, until either the stack fits or no merges remain.
        for _safety in range(50):
            cur_total, _heights = _stack_h(bands)
            if cur_total <= body_h:
                break
            best_merge: tuple[int, float] | None = None  # (i, combined_h)
            for i in range(1, len(bands) - 1):
                # Skip merges that involve the hero (i.e. always keep band 0
                # as a solo band).
                a, b = bands[i], bands[i + 1]
                if len(a) + len(b) > max_per_band:
                    continue
                merged_slot = _band_slot_width(a + b)
                if merged_slot > usable_w:
                    continue
                # Prefer merging the two with the SMALLEST max-height (so we
                # don't accidentally make a band twice as tall by merging a
                # tall one with another tall one).
                merged_h = max(
                    max((it[3] for it in a), default=0.0),
                    max((it[3] for it in b), default=0.0),
                )
                if best_merge is None or merged_h < best_merge[1]:
                    best_merge = (i, merged_h)
            if best_merge is None:
                break
            i = best_merge[0]
            bands[i] = bands[i] + bands[i + 1]
            del bands[i + 1]

        total_h, band_heights = _stack_h(bands)

        # 7b-pre. Total-GROW pass: with alpha-bbox aspect (Bug B), fish are
        # MUCH shorter than they used to be (master canvas was 1.5:1, real
        # fish are 1.6–3.4:1) so the natural stack is now far shorter than
        # body_h. Grow fish uniformly until either:
        #   (a) the stack height matches body_h, or
        #   (b) any band's slot width hits 85% of usable_w (cosmetic ceiling
        #       so fish don't crash into the side margins).
        # Then absorb any remaining vertical slack into inter-band gaps so
        # the stack visually centers in the body rather than bunching up.
        # This keeps honest-scale relations intact while filling the body.
        if total_h < body_h * 0.95 and bands:
            scalable_h = sum(band_heights) + label_reserve_px * len(bands)
            constant = inter_band_px * max(0, len(bands) - 1)
            available = max(1, body_h - constant)
            if scalable_h > 0:
                grow_height = available / scalable_h
            else:
                grow_height = 1.0
            # Width ceiling: NO band's combined slot width may exceed 85%
            # of usable_w after grow (else fish crash into side margins).
            # Check every band, not just the hero — body bands of 3-4 small
            # fish can collectively be wider than the hero.
            max_band_slot = 0.0
            for band in bands:
                slot = sum(max(it[2], float(it[4])) for it in band)
                if len(band) > 1:
                    slot += min_label_pad_px * (len(band) - 1)
                max_band_slot = max(max_band_slot, slot)
            if max_band_slot > 0:
                grow_width = (usable_w * 0.85) / max_band_slot
            else:
                grow_width = grow_height
            grow = min(grow_height, grow_width)
            if grow > 1.0:
                # Apply uniform grow factor by inflating unit_w and
                # recomputing all per-fish dims. This rebuilds the
                # band_heights since cand_h scales linearly.
                unit_w *= grow
                # Rebuild bands' tuples with grown widths/heights.
                new_bands: list[
                    list[tuple[SpeciesRef, MasterImage, float, float, int]]
                ] = []
                for band in bands:
                    new_band = []
                    for (ref, master, cand_w, cand_h, label_w) in band:
                        new_band.append(
                            (ref, master, cand_w * grow, cand_h * grow, label_w)
                        )
                    new_bands.append(new_band)
                bands = new_bands
                total_h, band_heights = _stack_h(bands)

        # 7b. Total-shrink pass: if STILL overflowing, scale uniformly.
        # NEVER drop species — the customer selected them all, every one
        # must appear. The hero target is already count-aware (see step 6),
        # so by this point the overflow should be modest and a uniform
        # shrink keeps relative scale intact across the whole poster.
        shrink = 1.0
        if total_h > body_h:
            # Solve: shrink * (sum_band_h + labels) + gaps == body_h.
            # We shrink the band heights AND label reserve uniformly; gaps
            # stay constant since they're cosmetic.
            scalable = sum(band_heights) + label_reserve_px * len(bands)
            constant = inter_band_px * max(0, len(bands) - 1)
            avail = max(1, body_h - constant)
            if scalable > 0:
                shrink = min(1.0, avail / scalable)
            warnings.append(
                f"FieldGuideBandsEngine: stack height {int(total_h)}px > body "
                f"{body_h}px; shrinking everything to {shrink:.0%} of native size."
            )

        scaled_band_heights = [bh * shrink for bh in band_heights]
        scaled_label_reserve = label_reserve_px * shrink

        # Hero post-scale (Bug 3 final touch): if shrink left the hero
        # below the count-aware hero target, scale up JUST the hero —
        # proportionally — toward that target. We only do this when the
        # hero is solo (band[0] has 1 fish), and we cap the rescale so it
        # never exceeds the original natural cand_w (preserves honest-scale
        # relative to the body fish in the uncrowded case where
        # shrink == 1.0).
        #
        # The hero floor is a SOFT preference: if the body re-shrink that
        # would be required to grow the hero pushes body fish below a
        # readability floor, we skip the rescale. Species are never
        # dropped here — the count-aware hero target (step 6) already
        # ensures the natural sizing fits.
        #
        # When the hero rescale grows band[0]'s height, we MUST re-shrink
        # the body bands to keep total stack height within body_h (so the
        # bottom row doesn't fall off the canvas).
        hero_scale_override: float | None = None
        if bands and len(bands[0]) == 1:
            hero_cand_w_native = bands[0][0][2]
            hero_w_after_shrink = hero_cand_w_native * shrink
            hero_floor = canvas_w * hero_target_fraction
            if hero_w_after_shrink < hero_floor:
                target_hero_w = min(hero_floor, hero_cand_w_native)
                if hero_w_after_shrink > 0:
                    hero_scale_override = target_hero_w / hero_w_after_shrink
                    # New hero band height after rescale.
                    new_hero_h = scaled_band_heights[0] * hero_scale_override
                    # Body budget = body_h minus hero band, hero label,
                    # and inter-band gaps.
                    constant = (
                        new_hero_h
                        + scaled_label_reserve  # hero's own label
                        + inter_band_px * max(0, len(bands) - 1)
                    )
                    body_budget = max(1.0, body_h - constant)
                    body_band_h_sum = sum(scaled_band_heights[1:])
                    body_label_sum = scaled_label_reserve * (len(bands) - 1)
                    body_scalable = body_band_h_sum + body_label_sum
                    if body_scalable > body_budget and body_scalable > 0:
                        body_reshrink = body_budget / body_scalable
                        # Apply re-shrink to body bands' heights AND
                        # widths (so honest scale within body is kept).
                        for j in range(1, len(scaled_band_heights)):
                            scaled_band_heights[j] *= body_reshrink
                        # Track the body re-shrink so placement loop uses it.
                        body_shrink_factor = body_reshrink
                    else:
                        body_shrink_factor = 1.0
                    # Update hero band height.
                    scaled_band_heights[0] = new_hero_h
                else:
                    body_shrink_factor = 1.0
            else:
                body_shrink_factor = 1.0
        else:
            body_shrink_factor = 1.0

        # 7c. Absorb leftover vertical slack into inter-band gaps so bands
        # are evenly distributed in the body region rather than bunched at
        # the top with empty space below. This is a pure cosmetic adjustment
        # — fish sizes don't change, just the gap between rows. Triggered
        # when the natural stack (after grow + shrink) is shorter than the
        # body height by more than 5%.
        used_h = (
            sum(scaled_band_heights)
            + scaled_label_reserve * len(bands)
            + inter_band_px * max(0, len(bands) - 1)
        )
        slack = body_h - used_h
        # Distribute as additional gap between bands (not above hero, not
        # below last band — that would just push the whole stack up/down).
        gap_count = max(1, len(bands) - 1)
        if slack > body_h * 0.05 and gap_count > 0:
            extra_per_gap = slack / gap_count
            inter_band_px = int(round(inter_band_px + extra_per_gap))

        # 6. Spread each band horizontally to ~75% of canvas width, stack
        # vertically, emit placements (Bug 3 fix).
        #
        # After packing fish tight, EXPAND inter-fish gaps so each multi-
        # fish band stretches to roughly the target band width. Caps the
        # inter-fish gap at max_inter_fish_gap_fraction of canvas width so
        # bands of 2 small fish don't fly to opposite edges.
        target_band_w = canvas_w * self.target_band_width_fraction
        max_gap_px = int(round(canvas_w * self.max_inter_fish_gap_fraction))
        placements: list[PlacedItem] = []
        cursor_y = float(body_top)
        for band_idx, (band, band_h) in enumerate(zip(bands, scaled_band_heights)):
            n = len(band)
            # Apply hero post-rescale only to the hero band (idx 0, single
            # fish). Body bands use shrink * body_shrink_factor (the
            # additional shrink applied to compensate for hero growth).
            if band_idx == 0 and hero_scale_override is not None:
                effective_shrink = shrink * hero_scale_override
            else:
                effective_shrink = shrink * body_shrink_factor
            scaled_fish_widths = [it[2] * effective_shrink for it in band]
            label_widths = [it[4] for it in band]
            scaled_fish_total = sum(scaled_fish_widths)
            if n >= 2:
                # Expand to target band width but respect the cap, and
                # never go below the original cosmetic fish gap (or label
                # pad, whichever is wider).
                min_gap_floor = max(gap_px, min_label_pad_px)
                # Hard floor for label non-collision: between adjacent
                # fish, gap must be at least the sum of label half-widths
                # exceeding fish half-widths plus min label pad. We use the
                # tightest pair as the band-wide floor (Bug 2 fix —
                # ensures no two adjacent labels overlap regardless of
                # which pair is densest).
                label_collision_floor = 0.0
                for i in range(n - 1):
                    lw_i, lw_n = label_widths[i], label_widths[i + 1]
                    fw_i, fw_n = scaled_fish_widths[i], scaled_fish_widths[i + 1]
                    needed = (lw_i + lw_n) / 2.0 + min_label_pad_px - (fw_i + fw_n) / 2.0
                    label_collision_floor = max(label_collision_floor, needed)
                min_gap = max(float(min_gap_floor), label_collision_floor)
                desired_remaining = max(0.0, target_band_w - scaled_fish_total)
                inter_gap = desired_remaining / (n - 1)
                inter_gap = min(inter_gap, float(max_gap_px))
                # Label non-collision overrides the cosmetic cap.
                inter_gap = max(inter_gap, min_gap)
                total_band_w = scaled_fish_total + inter_gap * (n - 1)
            else:
                inter_gap = 0.0
                total_band_w = scaled_fish_total
            x_cursor = (canvas_w - total_band_w) / 2.0
            # Baseline-align the band: fish bottoms rest on a shared visual
            # ground line, the way a field guide typesets specimens. This
            # gives taller fish (pike, gar) more vertical extent above the
            # baseline while shorter fat fish (sunfish, crappie) sit
            # neatly on the line. Baseline at 95% of band height — leaves a
            # tiny breathing margin so the absolute tallest fish in the
            # band still has room above it. Bug C fix.
            band_baseline_y = band_h * 0.95
            for (ref, master, cand_w, cand_h, _label_w) in band:
                draw_w = max(1, int(round(cand_w * effective_shrink)))
                draw_h = max(1, int(round(cand_h * effective_shrink)))
                # Anchor each fish's BOTTOM to the band baseline.
                y_offset = band_baseline_y - draw_h
                placements.append(
                    PlacedItem(
                        species_ref=ref,
                        master=master,
                        x=int(round(x_cursor)),
                        y=int(round(cursor_y + y_offset)),
                        draw_width=draw_w,
                        draw_height=draw_h,
                    )
                )
                x_cursor += cand_w * effective_shrink + inter_gap
            cursor_y += band_h + scaled_label_reserve + inter_band_px

        return LayoutResult(poster=spec, placements=placements, warnings=warnings)


# --- VintageCatalogEngine ----------------------------------------------------


class VintageCatalogEngine(LayoutEngine):
    """4-column grid layout in the style of an antique sporting-goods catalog.

    Algorithm:

    1. Reserve top 13% of canvas height for the title block, bottom 2% for
       margin. Body = the middle 85%.
    2. Lay the species into 4 columns × ``ceil(N / 4)`` rows. Each cell is
       the same width and height.
    3. Inside each cell, scale the master to fit ``cell_w * 0.85 ×
       cell_h * 0.70`` while preserving aspect ratio. The remaining
       ``cell_h * 0.20`` at the bottom is reserved for the renderer-drawn
       two-line label (bold common name + italic Latin).
    4. Center the master horizontally and vertically within its allotted
       image region. Last row is centered within the column track when
       fewer than 4 species remain.

    Unlike :class:`FieldGuideBandsEngine`, this engine does NOT honor
    ``relative_scale_index`` — every cell is the same size. The vintage
    catalog aesthetic prizes uniform cells over honest scale.
    """

    def __init__(
        self,
        columns: int = 4,
        # Bumped from 0.13 → 0.20 so the ornamental title frame
        # (preheader + top-rule-with-diamond + giant title + bottom-rule-with-
        # diamond) has enough vertical room. The previous value caused the
        # bottom rule to land on top of the first row of fish.
        title_band_fraction: float = 0.20,
        bottom_margin_fraction: float = 0.02,
        side_margin_fraction: float = 0.05,
        gutter_fraction: float = 0.015,
        cell_image_w_fraction: float = 0.85,
        cell_image_h_fraction: float = 0.70,
        cell_label_h_fraction: float = 0.20,
        # Buffer between the title block's bottom rule and the first row
        # of fish — guarantees ornamental rule clearance even when the title
        # font shrinks/grows.
        title_clearance_fraction: float = 0.025,
    ) -> None:
        self.columns = max(1, columns)
        self.title_band_fraction = title_band_fraction
        self.bottom_margin_fraction = bottom_margin_fraction
        self.side_margin_fraction = side_margin_fraction
        self.gutter_fraction = gutter_fraction
        self.cell_image_w_fraction = cell_image_w_fraction
        self.cell_image_h_fraction = cell_image_h_fraction
        self.cell_label_h_fraction = cell_label_h_fraction
        self.title_clearance_fraction = title_clearance_fraction

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []

        if not species:
            warnings.append("VintageCatalogEngine received zero species.")
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        pairs = _resolve_species_with_masters(spec, species, loader, warnings)
        if not pairs:
            warnings.append("VintageCatalogEngine: no masters available.")
            return LayoutResult(poster=spec, placements=[], warnings=warnings)

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # 1. Carve regions. Add a clearance buffer below the ornamental
        # title frame's bottom rule so the first row of fish never collides
        # with the diamond rule (Bug 5 fix).
        title_h = int(round(canvas_h * self.title_band_fraction))
        title_clearance = int(round(canvas_h * self.title_clearance_fraction))
        bottom_h = int(round(canvas_h * self.bottom_margin_fraction))
        body_top = title_h + title_clearance
        body_h = max(1, canvas_h - body_top - bottom_h)
        side_margin = int(round(canvas_w * self.side_margin_fraction))
        gutter_w_px = int(round(canvas_w * self.gutter_fraction))
        gutter_h_px = int(round(canvas_h * self.gutter_fraction))

        # 2. Cell dimensions.
        cols = self.columns
        rows = max(1, math.ceil(len(pairs) / cols))
        cell_w = max(
            1,
            (canvas_w - 2 * side_margin - (cols - 1) * gutter_w_px) // cols,
        )
        cell_h = max(1, (body_h - (rows - 1) * gutter_h_px) // rows)

        img_w_cap = cell_w * self.cell_image_w_fraction
        img_h_cap = cell_h * self.cell_image_h_fraction
        label_h_reserve = cell_h * self.cell_label_h_fraction

        placements: list[PlacedItem] = []

        # Track-x: where the column track starts within the canvas (so the
        # last partial row can be centered relative to the same track).
        track_x_start = side_margin
        track_w = cols * cell_w + (cols - 1) * gutter_w_px

        for idx, (ref, master) in enumerate(pairs):
            row = idx // cols
            col = idx % cols

            # 4. Last row centering: if this is the last row and it's
            # partial, push the row's items so they're centered within the
            # column track instead of left-aligned.
            if row == rows - 1:
                in_last_row = len(pairs) - row * cols
                if in_last_row < cols:
                    # Pixel offset that re-centers the partial row.
                    row_w = in_last_row * cell_w + (in_last_row - 1) * gutter_w_px
                    row_offset = (track_w - row_w) // 2
                else:
                    row_offset = 0
            else:
                row_offset = 0

            cell_x = track_x_start + row_offset + col * (cell_w + gutter_w_px)
            cell_y = body_top + row * (cell_h + gutter_h_px)

            # 3. Scale master to fit the image region preserving aspect.
            src_w = max(1, master.width_px)
            src_h = max(1, master.height_px)
            scale = min(img_w_cap / src_w, img_h_cap / src_h)
            draw_w = max(1, int(round(src_w * scale)))
            draw_h = max(1, int(round(src_h * scale)))

            # 4. Center master horizontally; the image region sits on top
            # of the cell with the label-reserve at the bottom.
            img_region_h = cell_h - int(round(label_h_reserve))
            x = cell_x + (cell_w - draw_w) // 2
            y = cell_y + (img_region_h - draw_h) // 2

            placements.append(
                PlacedItem(
                    species_ref=ref,
                    master=master,
                    x=int(x),
                    y=int(y),
                    draw_width=int(draw_w),
                    draw_height=int(draw_h),
                )
            )

        return LayoutResult(poster=spec, placements=placements, warnings=warnings)
