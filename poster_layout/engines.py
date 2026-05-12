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
    """Deterministic HERO + adaptive PAIR/SINGLE/TRIPLE/DOUBLE zigzag layout.

    Inspired by the reference poster at ``output/uploads/fish poster.jpeg``
    but with an adaptive enhancement: rows of small fish use 3-across triples
    (with 2-across doubles staggered between them) so the bottom of the poster
    reads as a denser "field guide" page when many small species are present.

    Layout pattern (sorted largest-first by relative_scale_index)::

        Row 0   — HERO: largest fish, centered, ~hero_target_w_fraction wide
        Row 1   — PAIR (2 at L/R edges)
        Row 1.5 — SINGLE (1 centered, between rows 1 and 2)
        Row 2   — PAIR
        ...
        (transition: SINGLE if next 3 are "small")
        Row k   — TRIPLE (3 across: L, C, R)
        Row k.5 — DOUBLE (2 staggered into the gaps between triple positions)
        Row k+1 — TRIPLE
        ...

    "Small" is defined per fish: ``draw_width < canvas_w * small_fish_threshold_fraction``.
    When the next 3 fish in the sorted queue are all small, an entire
    TRIPLE/DOUBLE cycle is emitted instead of PAIR/SINGLE.

    Pairs with :class:`EditorialMultiRenderer` rendering with the
    "field_guide" :class:`StyleProfile`.
    """

    def __init__(
        self,
        # Top reserved for the title block.
        title_band_fraction: float = 0.20,
        # Bottom margin. Mirrors the side margin's "inner border inset
        # + inner buffer" structure (0.030 outer + 0.030 inner buffer
        # measured in canvas_w units). On a 3:4 portrait canvas
        # (canvas_h = canvas_w * 1.333) the same pixel buffer becomes
        # 0.06 / 1.333 = 0.045 of canvas_h.
        bottom_margin_fraction: float = 0.045,
        # Side gutter. Hugs the L/R edge fish in pair / triple rows.
        side_margin_fraction: float = 0.060,
        # Honest-scale floor so the smallest fish remain substantial.
        min_idx_floor: float = 0.4,
        # Compress the relative_scale_index range via power.
        # 0.50 yields a ~2:1 bluegill→pike ratio (tighter than the 0.65
        # default's ~2.84:1 — closer to the reference poster's even read).
        scale_compression: float = 0.30,
        # Hero fish target draw-width as a fraction of canvas_w. 0.65 (was
        # 0.55) so fish read bigger across the board — the prior default
        # left visible whitespace after the shrink-to-fit pass.
        hero_target_w_fraction: float = 0.65,
        # Gap (canvas-fraction) between L/R fish in a pair row. Tightened
        # from 0.45 → 0.30 so pair fish are bigger.
        pair_separation_fraction: float = 0.30,
        # Where a single fish sits vertically between two adjacent main rows.
        single_y_offset_fraction: float = 0.50,
        # Fish whose draw_width is below this fraction of canvas_w count
        # as "small" and trigger triple-row mode.
        small_fish_threshold_fraction: float = 0.22,
        # ---- Back-compat (accepted, silently unused by the new layout) ----
        target_fish_per_band: int | None = None,
        max_fish_per_band: int | None = None,
        max_inter_fish_gap_fraction: float | None = None,
        target_band_width_fraction: float | None = None,
        label_band_fraction: float | None = None,
        inter_band_gap_fraction: float | None = None,
        inter_fish_gap_fraction: float | None = None,
        title_breathing_fraction: float | None = None,
        min_label_pad_fraction: float | None = None,
    ) -> None:
        self.title_band_fraction = title_band_fraction
        self.bottom_margin_fraction = bottom_margin_fraction
        self.side_margin_fraction = side_margin_fraction
        self.min_idx_floor = min_idx_floor
        self.scale_compression = scale_compression
        self.hero_target_w_fraction = hero_target_w_fraction
        self.pair_separation_fraction = pair_separation_fraction
        self.single_y_offset_fraction = single_y_offset_fraction
        self.small_fish_threshold_fraction = small_fish_threshold_fraction
        # Back-compat — surface kept for older callers.
        self.target_fish_per_band = target_fish_per_band
        self.max_fish_per_band = max_fish_per_band
        self.max_inter_fish_gap_fraction = max_inter_fish_gap_fraction
        self.target_band_width_fraction = target_band_width_fraction
        self.label_band_fraction = label_band_fraction
        self.inter_band_gap_fraction = inter_band_gap_fraction
        self.inter_fish_gap_fraction = inter_fish_gap_fraction
        self.title_breathing_fraction = title_breathing_fraction
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

        # 1. Sort by water column (top→mid→bottom), then by size desc within
        # each tier. This makes the poster read top→bottom of the water
        # column: surface predators up top, mid-water fish in the middle,
        # bottom feeders at the foot.
        #
        # Hero override: the BIGGEST fish in the entire selection is hoisted
        # to slot 0 regardless of its water column, so the eye lands on the
        # poster's most impressive specimen. Everything else is water-column
        # sorted behind it.
        WATER_RANK = {"top": 0, "mid": 1, "bottom": 2}

        def _wc_rank(ref: SpeciesRef) -> int:
            return WATER_RANK.get(getattr(ref, "water_column", "mid"), 1)

        biggest_idx = max(
            range(len(pairs)),
            key=lambda i: pairs[i][0].relative_scale_index,
        )
        hero_pair = pairs[biggest_idx]
        rest = [p for i, p in enumerate(pairs) if i != biggest_idx]
        rest_sorted = sorted(
            rest,
            key=lambda pr: (_wc_rank(pr[0]), -pr[0].relative_scale_index),
        )
        pairs_sorted = [hero_pair] + rest_sorted

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # 2. Carve regions.
        title_h = int(round(canvas_h * self.title_band_fraction))
        bottom_margin = int(round(canvas_h * self.bottom_margin_fraction))
        body_top = title_h
        body_bottom = canvas_h - bottom_margin
        body_h = max(1, body_bottom - body_top)

        # Reserve room for the trailing fish's caption (common + italic
        # latin) — keep label inside the body. Matches the renderer's
        # _draw_compact_caption_with_latin sizes (canvas_h * 0.010 for
        # common + 0.0075 for latin + ~0.006 padding + 0.003 line gap).
        label_reserve = int(round(canvas_h * 0.026))

        side_margin = int(round(canvas_w * self.side_margin_fraction))
        # Inner-border buffer: equal to the gap between the inner border
        # line and the canvas edge (0.030 of canvas_w). The user wants
        # this same buffer between the inner border and the content on
        # all four sides — so top fish butts against this buffer below
        # the title, bottom fish + label butts against it above the
        # inner border, and pair fish respect it horizontally (already
        # the case since side_margin = 0.060 = 0.030 + 0.030 buffer).
        inner_buffer_px = int(round(canvas_w * 0.030))

        # 3. Compressed honest scale.
        _c = max(0.1, min(1.0, self.scale_compression))
        idx_for = lambda ref: max(self.min_idx_floor, ref.relative_scale_index) ** _c

        # 4. Size every fish first so we can classify "small" by draw_width.
        # The widest fish in the catalog (typically the hero or the most-
        # compressed-large like the carp) is sized to FILL the usable
        # canvas width — exactly hugging the inner buffer on both sides.
        # That's the "maximum fish size that does not infringe on the
        # buffer" constraint, per user direction. Vertical stack then
        # shrinks to fit body_h if necessary; otherwise grows.
        N = len(pairs_sorted)
        hero_ref, hero_master = pairs_sorted[0]
        largest_idx = idx_for(hero_ref)
        usable_w = canvas_w - 2 * side_margin
        # The widest fish across all species is whichever has the
        # largest compressed-idx (== hero in our pre-sort). Target that
        # fish at exactly usable_w so it butts up against the buffer.
        target_hero_w = float(usable_w)
        unit_w = target_hero_w / max(1e-6, largest_idx)

        def fish_dims(ref: SpeciesRef, master: MasterImage) -> tuple[float, float]:
            bw = max(1, master.bbox_width_px)
            bh = max(1, master.bbox_height_px)
            aspect = bw / bh  # width / height
            draw_w = unit_w * idx_for(ref)
            draw_h = draw_w / max(1e-6, aspect)
            return draw_w, draw_h

        hero_w, hero_h = fish_dims(hero_ref, hero_master)
        non_hero_pairs = pairs_sorted[1:]
        non_hero_dims = [fish_dims(ref, m) for (ref, m) in non_hero_pairs]
        small_threshold_w = canvas_w * self.small_fish_threshold_fraction

        def _is_small(idx: int) -> bool:
            return non_hero_dims[idx][0] < small_threshold_w

        # 5. Adaptive slot assignment for the N-1 non-hero fish.
        # Modes: pair/single zigzag for medium+ fish; triple/double zigzag
        # for small fish; a "transition single" sits between the two modes.
        slots: list[str] = []  # parallel to non_hero_pairs / non_hero_dims
        idx = 0
        next_kind = "pair"  # "pair" | "single" | "triple" | "double"
        n = len(non_hero_pairs)

        def _next3_all_small(start: int) -> bool:
            if start + 3 > n:
                return False
            return all(_is_small(start + k) for k in range(3))

        while idx < n:
            remaining = n - idx
            if next_kind == "pair":
                # If the next 3 are all small, transition: emit a single
                # (if there's at least one fish left that's medium-ish)
                # then switch to triple mode. But the cleanest transition
                # is to skip pair and emit single → triple if the next
                # cohort is small.
                if _next3_all_small(idx):
                    # Transition single (if a fish remains) then triple.
                    next_kind = "single_to_triple"
                    continue
                if remaining >= 2:
                    slots.append("pair_L")
                    slots.append("pair_R")
                    idx += 2
                    next_kind = "single"
                elif remaining == 1:
                    slots.append("single")
                    idx += 1
                    next_kind = "pair"
            elif next_kind == "single":
                slots.append("single")
                idx += 1
                # Decide next mode: triple if next 3 are small, else pair.
                if _next3_all_small(idx):
                    next_kind = "triple"
                else:
                    next_kind = "pair"
            elif next_kind == "single_to_triple":
                # Emit one transition single (uses the largest of the
                # next batch — i.e. the current head, which is just
                # barely under the small threshold or borderline).
                slots.append("single")
                idx += 1
                next_kind = "triple"
            elif next_kind == "triple":
                if remaining >= 3 and _next3_all_small(idx):
                    slots.append("triple_L")
                    slots.append("triple_C")
                    slots.append("triple_R")
                    idx += 3
                    next_kind = "double"
                elif remaining >= 2:
                    # Not enough small fish for a full triple — fall back
                    # to pair for cleanup.
                    slots.append("pair_L")
                    slots.append("pair_R")
                    idx += 2
                    next_kind = "single"
                else:
                    slots.append("single")
                    idx += 1
                    next_kind = "pair"
            elif next_kind == "double":
                if remaining >= 2:
                    slots.append("double_L")
                    slots.append("double_R")
                    idx += 2
                    # After a double, look for another triple cycle; if
                    # not enough small fish remain, fall back to pair.
                    if _next3_all_small(idx):
                        next_kind = "triple"
                    else:
                        next_kind = "pair"
                else:
                    slots.append("single")
                    idx += 1
                    next_kind = "pair"

        # Combine two trailing singles into a final pair. The natural
        # alternation produces a tail like "... pair, single, single"
        # whenever N leaves a single after the last pair AND the prior
        # slot was also a single (happens for certain N counts, e.g.
        # 11 non-hero fish). Two centered singles stacked vertically
        # waste a row's worth of body height and force the rest of the
        # stack tighter; merging them into one L/R pair frees that
        # vertical budget for better distribution across the layout.
        if (
            len(slots) >= 2
            and slots[-1] == "single"
            and slots[-2] == "single"
        ):
            slots[-2] = "pair_L"
            slots[-1] = "pair_R"

        # Group slots into rows. Each row knows its kind + 1/2/3 fish entries.
        rows: list[dict] = []  # {"kind": "pair"|"single"|"triple"|"double", "fish": [...]}
        i = 0
        fi = 0  # index into non_hero_pairs
        while i < len(slots):
            s = slots[i]
            if s == "pair_L":
                row = {"kind": "pair", "fish": [non_hero_pairs[fi], None]}
                fi += 1
                i += 1
                if i < len(slots) and slots[i] == "pair_R":
                    row["fish"][1] = non_hero_pairs[fi]
                    fi += 1
                    i += 1
                rows.append(row)
            elif s == "triple_L":
                row = {"kind": "triple", "fish": [non_hero_pairs[fi], None, None]}
                fi += 1
                i += 1
                if i < len(slots) and slots[i] == "triple_C":
                    row["fish"][1] = non_hero_pairs[fi]
                    fi += 1
                    i += 1
                if i < len(slots) and slots[i] == "triple_R":
                    row["fish"][2] = non_hero_pairs[fi]
                    fi += 1
                    i += 1
                rows.append(row)
            elif s == "double_L":
                row = {"kind": "double", "fish": [non_hero_pairs[fi], None]}
                fi += 1
                i += 1
                if i < len(slots) and slots[i] == "double_R":
                    row["fish"][1] = non_hero_pairs[fi]
                    fi += 1
                    i += 1
                rows.append(row)
            elif s == "single":
                rows.append({"kind": "single", "fish": [non_hero_pairs[fi]]})
                fi += 1
                i += 1
            else:
                # Defensive: should not happen.
                i += 1

        # Pre-compute dimensions per row.
        for row in rows:
            dims = []
            for entry in row["fish"]:
                if entry is None:
                    dims.append((0.0, 0.0))
                else:
                    dims.append(fish_dims(*entry))
            row["dims"] = dims
            row["max_h"] = max((d[1] for d in dims), default=0.0)

        # 6. Compute vertical layout. Hero sits near the top; "main" rows
        # (pair + triple) fill the remaining body region; "stagger" rows
        # (single + double) sit at half-steps between adjacent main rows.

        MAIN_KINDS = {"pair", "triple"}
        STAGGER_KINDS = {"single", "double"}
        main_rows = [r for r in rows if r["kind"] in MAIN_KINDS]
        # Keep legacy names for code below.
        pair_rows = main_rows
        single_rows = [r for r in rows if r["kind"] in STAGGER_KINDS]
        P = len(pair_rows)

        # Hero Y-center: positioned near the top of the body region with
        # a little breathing room.
        hero_yc = body_top + hero_h * 0.55 + body_h * 0.02

        if P == 0:
            # Just hero, or hero + a single (rare). Place any single below
            # the hero with reasonable spacing.
            placements: list[PlacedItem] = []
            hero_x = int(round((canvas_w - hero_w) / 2.0))
            hero_y = int(round(hero_yc - hero_h / 2.0))
            placements.append(
                PlacedItem(
                    species_ref=hero_ref,
                    master=hero_master,
                    x=hero_x,
                    y=hero_y,
                    draw_width=int(round(hero_w)),
                    draw_height=int(round(hero_h)),
                )
            )
            cur_y = hero_y + int(round(hero_h)) + int(round(body_h * 0.05))
            for row in rows:
                for entry, (dw, dh) in zip(row["fish"], row["dims"]):
                    if entry is None:
                        continue
                    ref, master = entry
                    x = int(round((canvas_w - dw) / 2.0))
                    placements.append(
                        PlacedItem(
                            species_ref=ref,
                            master=master,
                            x=x,
                            y=cur_y,
                            draw_width=int(round(dw)),
                            draw_height=int(round(dh)),
                        )
                    )
                    cur_y += int(round(dh)) + int(round(body_h * 0.04))
            return LayoutResult(poster=spec, placements=placements, warnings=warnings)

        # Detect whether the layout ends on a trailing stagger row (single
        # or double). If it does, reserve y-space below the last main row.
        trailing_single_h = 0.0
        if rows and rows[-1]["kind"] in STAGGER_KINDS:
            trailing_single_h = rows[-1]["max_h"]

        # Helper: compute row y-centers given current dims. Returns
        # (hero_yc, row_y_centers, pair_ys) so the caller can inspect
        # whether the stack fits and re-run after shrinking.
        def compute_y_centers(
            local_hero_h: float,
            local_rows: list[dict],
            local_pair_rows: list[dict],
            local_trailing_single_h: float,
        ) -> tuple[float, list[float], list[float]]:
            # Hero anchors with its TOP edge at body_top + inner_buffer_px
            # (the same buffer as the side margin, per user request).
            # The top fish butts directly up against this buffer below
            # the title; no extra breathing.
            h_yc = body_top + inner_buffer_px + local_hero_h / 2.0
            P_local = len(local_pair_rows)
            if P_local == 0:
                return h_yc, [], []

            # ---- Even-distribution algorithm (user request) ------------
            # Place main rows evenly distributed across the body region
            # below the hero and above the bottom buffer. Staggers sit
            # at midpoints between adjacent mains automatically.

            # Index stagger heights so we know how much vertical room to
            # reserve at the trailing edge if there's a trailing stagger.
            stagger_between: list[float] = [0.0] * (P_local + 1)
            main_seen = 0
            for row in local_rows:
                if row["kind"] in MAIN_KINDS:
                    main_seen += 1
                else:
                    idx_between = main_seen
                    if idx_between <= P_local:
                        stagger_between[idx_between] = max(
                            stagger_between[idx_between], row["max_h"]
                        )

            # Top edge of main-stack: directly under the hero, with the
            # same inner_buffer between them as the side/top/bottom
            # buffer. First main fish butts up against this buffer.
            first_h = local_pair_rows[0]["max_h"]
            last_h = local_pair_rows[-1]["max_h"]

            # Bottom anchor: caption of the BOTTOM-MOST visible fish
            # (which is the trailing stagger if there is one, else the
            # last main row) butts against the inner buffer above the
            # inner border. So caption_bottom = body_bottom - inner_buffer.
            trailing_stagger_h = stagger_between[P_local]
            last_visible_h = max(
                last_h,
                trailing_stagger_h,
                local_trailing_single_h,
            )
            # Reserve below the LAST main row for trailing stagger(s).
            # When the slot pattern produces MULTIPLE trailing staggers
            # in a row (e.g. ...pair, single, single — happens when N
            # leaves a remainder that triggers two consecutive singles),
            # we must reserve room for all of them stacked: each fish's
            # body + each fish's caption (label_reserve) + breathing
            # gaps between. The trailing-most caption still bottoms out
            # exactly at body_bottom (handled via pair_bot formula).
            trailing_reserve = 0.0
            # Walk backward from the end of local_rows collecting
            # consecutive trailing stagger rows.
            _trail: list[dict] = []
            for r in reversed(local_rows):
                if r["kind"] in STAGGER_KINDS:
                    _trail.append(r)
                else:
                    break
            _trail.reverse()
            if _trail:
                # Bottom-most trailing stagger contributes only its body
                # height (its caption sits BELOW it, accounted for in
                # pair_bot via the standalone label_reserve term). Every
                # OTHER trailing stagger contributes body + label +
                # breathing.
                last_trail = _trail[-1]
                trailing_reserve = (
                    last_trail["max_h"] + body_h * 0.005
                )
                for extra in _trail[:-1]:
                    trailing_reserve += (
                        extra["max_h"] + label_reserve + body_h * 0.005
                    )

            # ---- Hero-inclusive even distribution ---------------------
            # Treat the hero as the FIRST slot in the vertical row
            # sequence (yc_0 = h_yc), then distribute the N main rows
            # as slots 1..N evenly between hero's yc and pair_bot.
            # Why: pinning pair_top tightly to hero_y_bot + inner_buffer
            # caused the binary search to bind on hero ↔ first_pair
            # x-overlap (gap was only 162px, needed 187px label_reserve),
            # leaving huge slack between bottom rows. Including hero in
            # the slot distribution amortizes the body height evenly
            # across N+1 slots, so the gap below hero scales with the
            # bottom slack. Result: max-fit scale jumps from ~0.38 to
            # ~0.65+ for short layouts (significant size increase).
            #
            # body_bottom is the bottom of usable content (the outer
            # 0.030 inner-border inset + 0.030 inner buffer were both
            # baked into bottom_margin_fraction = 0.045 on a 3:4
            # canvas). So the bottom-most fish's caption-bottom sits
            # AT body_bottom — pair_bot is the LAST main's yc such that
            # its label (+ any trailing stagger and its label) bottom
            # out exactly at body_bottom.
            pair_bot = (
                body_bottom
                - last_h / 2.0
                - label_reserve
                - trailing_reserve
            )

            if P_local == 1:
                # Single main: place exactly between hero and pair_bot.
                pys = [(h_yc + pair_bot) / 2.0]
            elif pair_bot > h_yc:
                # Even-distribute the N main rows + hero across the
                # available vertical. Hero is slot 0 (already at h_yc),
                # mains are slots 1..N at h_yc + i*step.
                step = (pair_bot - h_yc) / P_local
                pys = [h_yc + (i + 1) * step for i in range(P_local)]
            else:
                # Body can't fit even uniformly — bunch at top and let
                # stack_fits drive the binary search to a smaller scale.
                pys = [h_yc + (i + 1) * body_h * 0.05 for i in range(P_local)]

            # ---- Build per-row yc_list including stagger rows ----------
            yc_list: list[float] = []
            pair_i = 0
            trailing_prev_bot: float | None = None
            for row in local_rows:
                if row["kind"] in MAIN_KINDS:
                    yc_list.append(pys[pair_i])
                    pair_i += 1
                else:
                    if 1 <= pair_i < len(pys):
                        y_upper = pys[pair_i - 1]
                        y_lower = pys[pair_i]
                        yc = y_upper + (y_lower - y_upper) * self.single_y_offset_fraction
                    elif pair_i >= len(pys):
                        stagger_h = row["max_h"]
                        if trailing_prev_bot is None:
                            last_main_h = local_pair_rows[-1]["max_h"]
                            top = (
                                pys[-1]
                                + last_main_h / 2.0
                                + body_h * 0.005
                            )
                        else:
                            top = (
                                trailing_prev_bot
                                + label_reserve
                                + body_h * 0.005
                            )
                        yc = top + stagger_h / 2.0
                        yc = min(
                            yc,
                            body_bottom - stagger_h / 2.0 - label_reserve,
                        )
                        trailing_prev_bot = yc + stagger_h / 2.0
                    else:
                        yc = (h_yc + pys[0]) / 2.0
                    yc_list.append(yc)

            # ---- Stagger shift to bbox-no-overlap valid range ---------
            # The default midpoint position can place a stagger so its
            # bbox overlaps an adjacent main fish (when the main is wide
            # enough to enter the center x-column AND the gap between
            # mains is smaller than the stagger's height). For each
            # stagger, compute the valid yc range bounded by x-overlap
            # neighbors above and below, then clamp into the range. If
            # the range is non-empty, the stagger gets a feasible
            # position. If it's empty, we leave the stagger at the
            # midpoint and let stack_fits's pairwise check signal the
            # binary search to shrink. Doing this INSIDE compute_y_centers
            # (rather than as a post-pass) lets the binary search SEE the
            # shifted positions and converge on a higher scale.
            def _entry_x(row_local: dict, ei: int, w: float) -> float:
                k = row_local["kind"]
                if k == "pair":
                    return float(side_margin) if ei == 0 else float(canvas_w - side_margin - w)
                if k == "triple":
                    if ei == 0:
                        return float(side_margin)
                    if ei == 1:
                        return (canvas_w - w) / 2.0
                    return float(canvas_w - side_margin - w)
                if k == "double":
                    lc = side_margin + w / 2.0
                    rc = canvas_w - side_margin - w / 2.0
                    cc = canvas_w / 2.0
                    mid = (lc + cc) / 2.0 if ei == 0 else (cc + rc) / 2.0
                    return mid - w / 2.0
                return (canvas_w - w) / 2.0  # single

            # Build bbox list for all placed fish (hero + every row entry).
            inner_bboxes: list[tuple[float, float, float, float, int]] = []
            # (x0, y0, x1, y1, row_idx)  — row_idx = -1 for hero.
            hx_min = (canvas_w - hero_w) / 2.0
            inner_bboxes.append((
                hx_min, h_yc - local_hero_h / 2.0,
                hx_min + hero_w, h_yc + local_hero_h / 2.0,
                -1,
            ))
            for ri, (rr, yc_v) in enumerate(zip(local_rows, yc_list)):
                for ei, (entry, (w, h)) in enumerate(zip(rr["fish"], rr["dims"])):
                    if entry is None:
                        continue
                    xx = _entry_x(rr, ei, w)
                    inner_bboxes.append((
                        xx, yc_v - h / 2.0,
                        xx + w, yc_v + h / 2.0,
                        ri,
                    ))

            # For each stagger row, shift its yc into the valid range.
            # Constraint includes label_reserve clearance from any
            # x-overlapping fish (so the stack_fits universal pairwise
            # check sees a valid position and the binary search can
            # converge on a higher scale).
            for ri, rr in enumerate(local_rows):
                if rr["kind"] not in STAGGER_KINDS:
                    continue
                row_h = rr["max_h"]
                cur_yc = yc_list[ri]
                my_bboxes = [b for b in inner_bboxes if b[4] == ri]
                if not my_bboxes:
                    continue
                max_upper = float("-inf")
                min_lower = float("inf")
                for mb in my_bboxes:
                    mx0, my0, mx1, my1, _ = mb
                    for ob in inner_bboxes:
                        if ob[4] == ri:
                            continue
                        ox0, oy0, ox1, oy1, _ = ob
                        if mx1 <= ox0 or ox1 <= mx0:
                            continue
                        if oy1 <= my0 + 1:
                            if oy1 > max_upper:
                                max_upper = oy1
                        elif oy0 >= my1 - 1:
                            if oy0 < min_lower:
                                min_lower = oy0
                        else:
                            ob_yc = (oy0 + oy1) / 2.0
                            if ob_yc <= cur_yc:
                                if oy1 > max_upper:
                                    max_upper = oy1
                            else:
                                if oy0 < min_lower:
                                    min_lower = oy0
                # Add label_reserve to the constraint so stack_fits's
                # universal pairwise check passes after the shift —
                # otherwise binary search would converge at the scale
                # where the MIDPOINT position satisfies label_reserve,
                # negating most of the benefit of the shift.
                yc_min = (max_upper + label_reserve + row_h / 2.0) if max_upper != float("-inf") else float("-inf")
                yc_max = (min_lower - label_reserve - row_h / 2.0) if min_lower != float("inf") else float("inf")
                if yc_min <= yc_max:
                    new_yc = cur_yc
                    if cur_yc < yc_min:
                        new_yc = yc_min
                    elif cur_yc > yc_max:
                        new_yc = yc_max
                    if abs(new_yc - cur_yc) > 0.5:
                        yc_list[ri] = new_yc
                        # Update inner_bboxes so later staggers see this
                        # row's new position.
                        delta = new_yc - cur_yc
                        for k, b in enumerate(inner_bboxes):
                            if b[4] == ri:
                                inner_bboxes[k] = (b[0], b[1] + delta, b[2], b[3] + delta, b[4])

            return h_yc, yc_list, pys

        # 7. Determine whether stack fits. If not, iterative uniform shrink.
        def stack_fits(
            local_hero_h: float,
            local_rows: list[dict],
            local_pair_rows: list[dict],
            local_trailing_single_h: float,
        ) -> bool:
            h_yc, yc_list, pys = compute_y_centers(
                local_hero_h, local_rows, local_pair_rows, local_trailing_single_h
            )
            P_local = len(local_pair_rows)
            if P_local == 0:
                return True
            # Pair rows must be in order (top y-center monotonic).
            if any(pys[i + 1] <= pys[i] for i in range(len(pys) - 1)):
                return False
            # Top of hero inside body.
            if h_yc - local_hero_h / 2.0 < body_top - 1:
                return False
            # Every row bottom inside body and every row top inside body.
            # Bottom check INCLUDES label_reserve so the caption stays
            # above the inner-border bottom margin.
            for row, yc in zip(local_rows, yc_list):
                row_h = row["max_h"]
                if yc + row_h / 2.0 + label_reserve > body_bottom + 1:
                    return False
                if yc - row_h / 2.0 < body_top - 1:
                    return False
            # Enforce inter-row buffer between adjacent MAIN rows. The
            # half-step stagger between them rides at canvas-center
            # (different x-column), so it's not the constraint; what
            # matters is that two consecutive L-or-R-justified pair
            # fish have visual breathing room of at least inner_buffer_px
            # between them. The shrink/grow loops converge to a state
            # where this buffer is just satisfied — fish are maximal
            # size that respects the inner-row buffer.
            for i in range(len(pys) - 1):
                hi = local_pair_rows[i]["max_h"]
                hj = local_pair_rows[i + 1]["max_h"]
                main_i_bot = pys[i] + hi / 2.0
                main_next_top = pys[i + 1] - hj / 2.0
                if main_next_top - main_i_bot < inner_buffer_px - 1:
                    return False

            # Adjacent rows must not vertically overlap (the centered single
            # has free horizontal space, but if its bbox extends past the
            # neighbouring pair's fish bbox vertically AND the single's x
            # overlaps a pair-fish's x, they'd collide. The centered single
            # sits at canvas center while pair fish hug L/R edges — they
            # don't horizontally overlap UNLESS a pair fish is so wide it
            # crosses the centerline. We assume pairs don't cross center.)
            # We still enforce row_i bottom <= row_{i+1} top + small slack
            # to keep vertical reading rhythm clean.
            for i in range(len(local_rows) - 1):
                ya = yc_list[i]
                yb = yc_list[i + 1]
                ha = local_rows[i]["max_h"]
                hb = local_rows[i + 1]["max_h"]
                a_bot = ya + ha / 2.0
                b_top = yb - hb / 2.0
                # If the rows' y-bboxes overlap, check whether their
                # x-ranges actually collide. Pair fish hug L/R columns,
                # centered singles live in the middle — they're allowed
                # to vertically overlap so long as their x-ranges don't.
                if a_bot > b_top + 1:
                    a_kind = local_rows[i]["kind"]
                    b_kind = local_rows[i + 1]["kind"]
                    # Only main row pairs (pair/triple ↔ pair/triple) are
                    # forbidden from y-overlapping. Any pairing involving
                    # a stagger (single/double) is allowed to y-overlap
                    # because the stagger is centered and the main fish
                    # are on the outside (no x-collision).
                    if a_kind in MAIN_KINDS and b_kind in MAIN_KINDS:
                        return False

            # Universal pairwise stagger-vs-other constraint.
            # For every pair of fish whose x-bboxes overlap, require at
            # least label_reserve worth of y-gap between the lower edge
            # of the upper fish and the upper edge of the lower fish —
            # so the upper fish's caption (occupying label_reserve) fits
            # between them without crashing into the lower fish's top
            # fin/tail. This applies to: stagger↔adjacent-main (e.g.
            # centered bluegill vs wide carp), stagger↔stagger in the
            # same column (e.g. rock_bass↔bluegill in the centered
            # column), hero↔first-row when their x-bboxes overlap. The
            # shrink loop converges to the size where this is satisfied
            # for every x-overlapping pair.
            #
            # NOTE: we deliberately use label_reserve only (not
            # label_reserve + inner_buffer_px) — inner_buffer_px is
            # already enforced separately between MAIN rows above. This
            # keeps the constraint moderate so the layout doesn't
            # over-shrink.
            def _entry_x_for(row: dict, ei: int, w: float) -> float:
                k = row["kind"]
                if k == "pair":
                    return float(side_margin) if ei == 0 else float(canvas_w - side_margin - w)
                if k == "triple":
                    if ei == 0:
                        return float(side_margin)
                    if ei == 1:
                        return (canvas_w - w) / 2.0
                    return float(canvas_w - side_margin - w)
                if k == "double":
                    lc = side_margin + w / 2.0
                    rc = canvas_w - side_margin - w / 2.0
                    cc = canvas_w / 2.0
                    mid = (lc + cc) / 2.0 if ei == 0 else (cc + rc) / 2.0
                    return mid - w / 2.0
                return (canvas_w - w) / 2.0

            local_bboxes: list[tuple[float, float, float, float]] = []
            _hx = (canvas_w - hero_w) / 2.0
            local_bboxes.append((
                _hx, h_yc - local_hero_h / 2.0,
                _hx + hero_w, h_yc + local_hero_h / 2.0,
            ))
            for r, yc in zip(local_rows, yc_list):
                for ei, (entry, (w, h)) in enumerate(zip(r["fish"], r["dims"])):
                    if entry is None:
                        continue
                    x = _entry_x_for(r, ei, w)
                    local_bboxes.append((
                        x, yc - h / 2.0, x + w, yc + h / 2.0,
                    ))
            nb = len(local_bboxes)
            for i in range(nb):
                ax0, ay0, ax1, ay1 = local_bboxes[i]
                for j in range(i + 1, nb):
                    bx0, by0, bx1, by1 = local_bboxes[j]
                    # x-overlap?
                    if ax1 <= bx0 or bx1 <= ax0:
                        continue
                    # Determine upper vs lower by y-center.
                    if (ay0 + ay1) <= (by0 + by1):
                        u_y1, l_y0 = ay1, by0
                    else:
                        u_y1, l_y0 = by1, ay0
                    if l_y0 - u_y1 < label_reserve - 1:
                        return False
            return True

        # 7. MAX-SCALE binary search.
        # Find the largest uniform scale s ∈ [0.1, 1.0] such that
        # stack_fits(s * base_dims) returns True. This replaces the
        # old discrete shrink-by-0.94 / grow-by-1.05 loops with a
        # tight binary search that converges on the optimum size
        # given the buffer + label-clearance constraints encoded in
        # stack_fits. Hero is sized at usable_w when scale=1.0
        # (horizontal max), so 1.0 is the upper bound; non-hero fish
        # stay proportionally smaller via unit_w * idx.
        #
        # 20 iterations of binary search give resolution ≈ 1e-6, far
        # tighter than the coarse 0.94 step the old loop used. Result:
        # fish settle at the true max size that respects buffers, not
        # at the nearest 6%-down step.
        base_hero_w = hero_w
        base_hero_h = hero_h
        base_trailing = trailing_single_h
        base_rows_dims = [list(r["dims"]) for r in rows]
        base_rows_max_h = [r["max_h"] for r in rows]

        def _apply_scale(s: float) -> None:
            nonlocal hero_w, hero_h, trailing_single_h
            hero_w = base_hero_w * s
            hero_h = base_hero_h * s
            trailing_single_h = base_trailing * s
            for r, base_dims, base_mh in zip(rows, base_rows_dims, base_rows_max_h):
                r["dims"] = [(w * s, h * s) for (w, h) in base_dims]
                r["max_h"] = base_mh * s

        _apply_scale(1.0)
        if stack_fits(hero_h, rows, pair_rows, trailing_single_h):
            final_scale = 1.0
        else:
            lo, hi = 0.10, 1.0
            for _ in range(20):
                mid = (lo + hi) / 2.0
                _apply_scale(mid)
                if stack_fits(hero_h, rows, pair_rows, trailing_single_h):
                    lo = mid
                else:
                    hi = mid
            final_scale = lo
            _apply_scale(final_scale)

        if final_scale < 0.999:
            warnings.append(
                f"FieldGuideBandsEngine: max-fit scale={final_scale:.4f} "
                f"(buffer + label_reserve binding)."
            )

        usable_w = canvas_w - 2 * side_margin  # used by emit code below

        hero_yc, row_y_centers, pair_ys = compute_y_centers(
            hero_h, rows, pair_rows, trailing_single_h
        )

        # 7c. Stagger overlap-resolution shift.
        # The even-distribution layout above places stagger rows
        # (single/double) at the geometric midpoint between adjacent
        # main rows. But when a main fish is WIDE enough to enter the
        # center x-column, its bbox can overlap the centered stagger's
        # bbox (carp's tail crossing into bluegill's bottom fin). This
        # post-pass walks every stagger row and shifts its yc up or
        # down within the valid range bounded by x-overlapping fish
        # ABOVE (their bottoms) and BELOW (their tops). Only stagger
        # rows move — mains are anchored by even-distribution and the
        # hero is anchored at body_top + buffer. If the valid range is
        # empty (rare; means upper and lower x-overlaps mutually
        # constrain the stagger), no shift is applied and the existing
        # geometry stands. Uses bbox-no-overlap (NOT label_reserve) as
        # the constraint so we resolve fish silhouette collisions
        # without forcing a global shrink.
        def _row_entry_x(row: dict, ei: int, w: float) -> float:
            """Mirror of the emit-time x-placement logic so this shift
            pass uses the same x-positions the renderer will."""
            k = row["kind"]
            if k == "pair":
                return float(side_margin) if ei == 0 else float(canvas_w - side_margin - w)
            if k == "triple":
                if ei == 0:
                    return float(side_margin)
                if ei == 1:
                    return (canvas_w - w) / 2.0
                return float(canvas_w - side_margin - w)
            if k == "double":
                lc = side_margin + w / 2.0
                rc = canvas_w - side_margin - w / 2.0
                cc = canvas_w / 2.0
                mid = (lc + cc) / 2.0 if ei == 0 else (cc + rc) / 2.0
                return mid - w / 2.0
            return (canvas_w - w) / 2.0  # single

        # Build the bbox list for every placed fish using current yc's.
        _bboxes: list[dict] = []
        _hx = (canvas_w - hero_w) / 2.0
        _bboxes.append({
            "row_idx": -1, "x0": _hx, "y0": hero_yc - hero_h / 2.0,
            "x1": _hx + hero_w, "y1": hero_yc + hero_h / 2.0,
        })
        for _ri, (_row, _yc) in enumerate(zip(rows, row_y_centers)):
            for _ei, (_entry, (_w, _h)) in enumerate(zip(_row["fish"], _row["dims"])):
                if _entry is None:
                    continue
                _x = _row_entry_x(_row, _ei, _w)
                _bboxes.append({
                    "row_idx": _ri, "x0": _x, "y0": _yc - _h / 2.0,
                    "x1": _x + _w, "y1": _yc + _h / 2.0,
                })

        # Shift each stagger row's yc into the valid bbox-only range.
        for _ri, _row in enumerate(rows):
            if _row["kind"] not in STAGGER_KINDS:
                continue
            row_h = _row["max_h"]
            cur_yc = row_y_centers[_ri]
            # Find all bboxes belonging to this row.
            row_b = [b for b in _bboxes if b["row_idx"] == _ri]
            if not row_b:
                continue
            # Aggregate upper/lower x-overlapping constraints across
            # every entry in this stagger row.
            max_upper_y1 = float("-inf")
            min_lower_y0 = float("inf")
            for rb in row_b:
                for ob in _bboxes:
                    if ob["row_idx"] == _ri:
                        continue
                    # x-overlap?
                    if rb["x1"] <= ob["x0"] or ob["x1"] <= rb["x0"]:
                        continue
                    # ob above this stagger?
                    if ob["y1"] <= rb["y0"] + 1:
                        if ob["y1"] > max_upper_y1:
                            max_upper_y1 = ob["y1"]
                    elif ob["y0"] >= rb["y1"] - 1:
                        if ob["y0"] < min_lower_y0:
                            min_lower_y0 = ob["y0"]
                    else:
                        # ob currently overlaps stagger bbox; treat as
                        # the binding constraint on whichever side is
                        # closer to its own center.
                        ob_yc = (ob["y0"] + ob["y1"]) / 2.0
                        if ob_yc <= cur_yc:
                            if ob["y1"] > max_upper_y1:
                                max_upper_y1 = ob["y1"]
                        else:
                            if ob["y0"] < min_lower_y0:
                                min_lower_y0 = ob["y0"]
            # Translate to yc bounds.
            yc_min = (max_upper_y1 + row_h / 2.0) if max_upper_y1 != float("-inf") else float("-inf")
            yc_max = (min_lower_y0 - row_h / 2.0) if min_lower_y0 != float("inf") else float("inf")
            # Clamp into the valid range, preferring the side that
            # moves us LEAST from the original midpoint.
            if yc_min <= yc_max:
                new_yc = cur_yc
                if cur_yc < yc_min:
                    new_yc = yc_min
                elif cur_yc > yc_max:
                    new_yc = yc_max
                if abs(new_yc - cur_yc) > 0.5:
                    row_y_centers[_ri] = new_yc
                    # Update this stagger's bboxes in the list so a
                    # later stagger sees its new position (in case two
                    # staggers x-overlap each other).
                    for rb in row_b:
                        delta = new_yc - cur_yc
                        rb["y0"] += delta
                        rb["y1"] += delta

        # 7d. Pair-centering horizontal shift.
        # Per user direction: pair fish should be PUSHED TOWARD THE
        # CENTER horizontally when possible. Default emit positions
        # have pair_L at x=side_margin and pair_R hugging the right
        # buffer — leaving a large empty gap in the middle. This pass
        # moves each pair fish inward (toward canvas_w/4 quadrant
        # center for L, 3*canvas_w/4 for R) as far as the label_reserve
        # constraint allows. If the shift would cause the fish's bbox
        # to x-overlap with a neighbor (hero, centered single, etc.)
        # while the y-gap is < label_reserve, the shift is reduced via
        # binary search. The result: pair fish read as a more
        # cohesive, centered composition while still respecting every
        # buffer / label clearance rule.
        #
        # Per-fish x positions are stored in `row_x_overrides[(ri, ei)]`
        # and used by the emit code below in lieu of the default
        # side_margin / canvas_w - side_margin - w positions.
        row_x_overrides: dict[tuple[int, int], float] = {}

        def _label_reserve_safe(
            new_x: float, w: float, h: float, yc: float,
            ignore_idx: int,
        ) -> bool:
            """Return True if placing a bbox at (new_x, yc) with the
            given (w, h) doesn't create any label_reserve y-gap
            violation with the other bboxes in _bboxes. ignore_idx is
            the position of the fish being moved (skip self-test)."""
            nx0, ny0, nx1, ny1 = new_x, yc - h / 2.0, new_x + w, yc + h / 2.0
            for idx_b, ob in enumerate(_bboxes):
                if idx_b == ignore_idx:
                    continue
                if nx1 <= ob["x0"] or ob["x1"] <= nx0:
                    continue
                # y-gap (positive = no overlap)
                if (ny0 + ny1) <= (ob["y0"] + ob["y1"]):
                    gap = ob["y0"] - ny1
                else:
                    gap = ny0 - ob["y1"]
                if gap < label_reserve - 1:
                    return False
            return True

        # Build an index of (row_idx, entry_idx) -> position in _bboxes.
        bbox_index: dict[tuple[int, int], int] = {}
        # _bboxes[0] is the hero; row entries follow in emit order.
        _pos = 1
        for _ri, _row in enumerate(rows):
            for _ei, _entry in enumerate(_row["fish"]):
                if _entry is None:
                    continue
                bbox_index[(_ri, _ei)] = _pos
                _pos += 1

        for _ri, _row in enumerate(rows):
            if _row["kind"] != "pair":
                continue
            L_entry, R_entry = _row["fish"]
            if L_entry is None or R_entry is None:
                continue
            (lw, lh) = _row["dims"][0]
            (rw, rh) = _row["dims"][1]
            yc = row_y_centers[_ri]

            # L: original at side_margin; target (quadrant center) at
            # canvas_w/4 - lw/2. Only shift INWARD (right).
            l_cur = float(side_margin)
            l_target = canvas_w / 4.0 - lw / 2.0
            if l_target > l_cur:
                idx_L = bbox_index.get((_ri, 0))
                if idx_L is not None:
                    # Binary search for max safe shift.
                    lo, hi = l_cur, l_target
                    # First check the target — fast path when constraints
                    # are loose enough to allow full inward shift.
                    if _label_reserve_safe(hi, lw, lh, yc, idx_L):
                        new_l = hi
                    else:
                        for _ in range(15):
                            mid = (lo + hi) / 2.0
                            if _label_reserve_safe(mid, lw, lh, yc, idx_L):
                                lo = mid
                            else:
                                hi = mid
                        new_l = lo
                    # Floor to integer so emit-time rounding can't push
                    # us past the float-safe boundary into bbox overlap.
                    new_l = float(math.floor(new_l))
                    if new_l - l_cur > 0.5:
                        row_x_overrides[(_ri, 0)] = new_l
                        # Update _bboxes so the R search sees the new L.
                        _bboxes[idx_L]["x0"] = new_l
                        _bboxes[idx_L]["x1"] = new_l + lw

            # R: original at canvas_w - side_margin - rw; target at
            # 3*canvas_w/4 - rw/2. Only shift INWARD (left).
            r_cur = float(canvas_w - side_margin - rw)
            r_target = 3.0 * canvas_w / 4.0 - rw / 2.0
            if r_target < r_cur:
                idx_R = bbox_index.get((_ri, 1))
                if idx_R is not None:
                    lo, hi = r_target, r_cur
                    # We binary-search the "safe" boundary, where safe
                    # is the LARGER (rightward, less inward) end.
                    if _label_reserve_safe(lo, rw, rh, yc, idx_R):
                        new_r = lo
                    else:
                        for _ in range(15):
                            mid = (lo + hi) / 2.0
                            if _label_reserve_safe(mid, rw, rh, yc, idx_R):
                                hi = mid
                            else:
                                lo = mid
                        new_r = hi
                    # Ceil to integer so emit-time rounding can't push
                    # us past the float-safe boundary into bbox overlap.
                    new_r = float(math.ceil(new_r))
                    if r_cur - new_r > 0.5:
                        row_x_overrides[(_ri, 1)] = new_r
                        _bboxes[idx_R]["x0"] = new_r
                        _bboxes[idx_R]["x1"] = new_r + rw

        # 7e. Bottom-pair even distribution override.
        # Special rule for the LAST row when it's a pair: instead of
        # using the pair-centering shift (which targets independent
        # quadrant centers and can yield asymmetric gaps when L and R
        # have different widths), distribute the two fish with THREE
        # equal horizontal gaps — left_margin == middle_gap ==
        # right_margin. The bottom of the poster reads as a balanced
        # final beat; the natural visual closure for the field guide.
        if rows and rows[-1]["kind"] == "pair":
            last_idx = len(rows) - 1
            last_row = rows[-1]
            L_entry, R_entry = last_row["fish"]
            if L_entry is not None and R_entry is not None:
                (lw, _lh) = last_row["dims"][0]
                (rw, _rh) = last_row["dims"][1]
                empty = canvas_w - lw - rw
                if empty > 0:
                    gap = empty / 3.0
                    # Verify the even-distribution positions don't
                    # create a new label_reserve violation. If they do,
                    # fall back to whatever the pair-centering pass
                    # already chose (or to the default L/R justification
                    # if no override exists).
                    new_l = math.floor(gap)
                    new_r = math.ceil(canvas_w - gap - rw)
                    yc_last = row_y_centers[last_idx]
                    idx_L = bbox_index.get((last_idx, 0))
                    idx_R = bbox_index.get((last_idx, 1))
                    if idx_L is not None and idx_R is not None:
                        # Temporarily move L; if safe, also try R.
                        save_l = (_bboxes[idx_L]["x0"], _bboxes[idx_L]["x1"])
                        _bboxes[idx_L]["x0"] = float(new_l)
                        _bboxes[idx_L]["x1"] = float(new_l) + lw
                        l_safe = _label_reserve_safe(
                            float(new_l), lw, last_row["dims"][0][1],
                            yc_last, idx_L,
                        )
                        if l_safe:
                            save_r = (_bboxes[idx_R]["x0"], _bboxes[idx_R]["x1"])
                            _bboxes[idx_R]["x0"] = float(new_r)
                            _bboxes[idx_R]["x1"] = float(new_r) + rw
                            r_safe = _label_reserve_safe(
                                float(new_r), rw, last_row["dims"][1][1],
                                yc_last, idx_R,
                            )
                            if r_safe:
                                row_x_overrides[(last_idx, 0)] = float(new_l)
                                row_x_overrides[(last_idx, 1)] = float(new_r)
                            else:
                                # Revert both.
                                _bboxes[idx_L]["x0"], _bboxes[idx_L]["x1"] = save_l
                                _bboxes[idx_R]["x0"], _bboxes[idx_R]["x1"] = save_r
                        else:
                            _bboxes[idx_L]["x0"], _bboxes[idx_L]["x1"] = save_l

        # 8. Emit placements. (The global 1.5x bump was baked into
        # target_hero_w earlier so the layout math saw the FINAL sizes;
        # no post-emit multiplier here.)
        placements = []
        hero_x = int(round((canvas_w - hero_w) / 2.0))
        hero_y_int = int(round(hero_yc - hero_h / 2.0))
        placements.append(
            PlacedItem(
                species_ref=hero_ref,
                master=hero_master,
                x=hero_x,
                y=hero_y_int,
                draw_width=int(round(hero_w)),
                draw_height=int(round(hero_h)),
            )
        )

        def _emit(entry, dw, dh, x_f, yc_f):
            ref, master = entry
            placements.append(
                PlacedItem(
                    species_ref=ref,
                    master=master,
                    x=int(round(x_f)),
                    y=int(round(yc_f - dh / 2.0)),
                    draw_width=int(round(dw)),
                    draw_height=int(round(dh)),
                )
            )

        # Pair fish: default to side_margin / canvas_w-side_margin
        # justification, but honor any inward shift computed by the
        # pair-centering pass above (row_x_overrides).
        for row_idx, (row, yc) in enumerate(zip(rows, row_y_centers)):
            kind = row["kind"]
            if kind == "pair":
                left_entry, right_entry = row["fish"]
                (lw, lh) = row["dims"][0]
                (rw, rh) = row["dims"][1]
                if left_entry is not None:
                    l_x = row_x_overrides.get((row_idx, 0), float(side_margin))
                    _emit(left_entry, lw, lh, l_x, yc)
                if right_entry is not None:
                    r_x = row_x_overrides.get(
                        (row_idx, 1), float(canvas_w - side_margin - rw)
                    )
                    _emit(right_entry, rw, rh, r_x, yc)
            elif kind == "triple":
                lE, cE, rE = row["fish"]
                (lw, lh) = row["dims"][0]
                (cw, ch) = row["dims"][1]
                (rw, rh) = row["dims"][2]
                lx = side_margin
                rx = canvas_w - side_margin - rw
                cx = (canvas_w - cw) / 2.0
                if lE is not None:
                    _emit(lE, lw, lh, row_x_overrides.get((row_idx, 0), float(lx)), yc)
                if cE is not None:
                    _emit(cE, cw, ch, cx, yc)
                if rE is not None:
                    _emit(rE, rw, rh, row_x_overrides.get((row_idx, 2), float(rx)), yc)
            elif kind == "double":
                # Position in the two gaps of the triple pattern: midway
                # between (L center) and (C center), and between (C center)
                # and (R center). We derive those center anchors from the
                # canvas geometry assuming a hypothetical triple at this y.
                lE, rE = row["fish"]
                (lw, lh) = row["dims"][0]
                (rw, rh) = row["dims"][1]
                # Anchor positions (same logic as triple_L / triple_C / triple_R).
                anchor_l_center = side_margin + lw / 2.0  # approx
                anchor_r_center = canvas_w - side_margin - rw / 2.0
                anchor_c_center = canvas_w / 2.0
                left_mid = (anchor_l_center + anchor_c_center) / 2.0
                right_mid = (anchor_c_center + anchor_r_center) / 2.0
                if lE is not None:
                    _emit(lE, lw, lh, left_mid - lw / 2.0, yc)
                if rE is not None:
                    _emit(rE, rw, rh, right_mid - rw / 2.0, yc)
            else:  # single
                entry = row["fish"][0]
                if entry is None:
                    continue
                (dw, dh) = row["dims"][0]
                _emit(entry, dw, dh, (canvas_w - dw) / 2.0, yc)

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
