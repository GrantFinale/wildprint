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
    """Pack species using their actual alpha silhouettes, not bounding boxes.

    Species can overlap at the bbox level if their alpha pixels don't
    conflict. Produces tight organic compositions like ranger-station
    wildlife posters where animals nest together.
    """

    def __init__(
        self,
        title_band_fraction: float = 0.10,
        caption_band_fraction: float = 0.07,
        side_margin_fraction: float = 0.03,
        packing_target: float = 0.85,
        scale_clamp_ratio: float = 4.0,
        min_visible_fraction: float = 0.25,
        overlap_tolerance: float = 0.02,
        mask_resolution: int = 120,
        label_height_px: int = 100,
    ) -> None:
        self.title_band_fraction = title_band_fraction
        self.caption_band_fraction = caption_band_fraction
        self.side_margin_fraction = side_margin_fraction
        self.packing_target = packing_target
        self.scale_clamp_ratio = scale_clamp_ratio
        self.min_visible_fraction = min_visible_fraction
        self.overlap_tolerance = overlap_tolerance
        self.mask_resolution = mask_resolution
        self.label_height_px = label_height_px

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        import numpy as np
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

        # 2. Sort largest-first.
        species_sorted = sorted(
            present, key=lambda r: r.relative_scale_index, reverse=True
        )

        # 3. Clamp scales.
        largest_scale = species_sorted[0].relative_scale_index
        floor = max(
            largest_scale * self.min_visible_fraction,
            largest_scale / self.scale_clamp_ratio,
        )
        effective: dict[str, float] = {}
        for s in species_sorted:
            effective[s.slug] = max(s.relative_scale_index, floor)

        # 4. Geometry.
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        title_h = int(round(canvas_h * self.title_band_fraction))
        caption_h = int(round(canvas_h * self.caption_band_fraction))
        side_margin_px = int(round(canvas_w * self.side_margin_fraction))
        content_w = canvas_w - 2 * side_margin_px
        content_h = canvas_h - title_h - caption_h
        content_x0 = side_margin_px
        content_y0 = title_h

        # 5. Load masters, compute tight-cropped aspect from alpha bbox.
        masters: dict[str, MasterImage] = {}
        cropped_aspects: dict[str, float] = {}
        alpha_crops: dict[str, Image.Image] = {}
        for s in species_sorted:
            master = loader.get(s.slug, spec.style_slug)
            masters[s.slug] = master
            try:
                with Image.open(master.image_path) as img:
                    alpha = img.convert("RGBA").split()[3]
                    bbox = alpha.getbbox()
                    if bbox:
                        alpha = alpha.crop(bbox)
                    alpha_crops[s.slug] = alpha.copy()
                    w_c, h_c = alpha.size
                    cropped_aspects[s.slug] = max(1, w_c) / max(1, h_c)
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"SilhouettePackedLayoutEngine: alpha load failed for "
                    f"'{s.slug}' ({exc}) — skipped."
                )
                continue

        # 6. Target draw sizes via area budget.
        total_weight = sum(effective[s.slug] ** 2 for s in species_sorted if s.slug in alpha_crops)
        if total_weight <= 0:
            return LayoutResult(poster=spec, placements=[], warnings=warnings)
        target_area = content_w * content_h * self.packing_target

        draws: dict[str, tuple[int, int]] = {}
        for s in species_sorted:
            if s.slug not in alpha_crops:
                continue
            weight = effective[s.slug] ** 2
            area = weight / total_weight * target_area
            aspect = cropped_aspects[s.slug]
            w = math.sqrt(area * aspect)
            h = w / aspect
            draws[s.slug] = (max(1, int(round(w))), max(1, int(round(h))))

        # 7. Mask resolution for occupancy grid — scale content area so
        # the longest axis equals (roughly) mask_resolution * aspect.
        if content_w >= content_h:
            mask_w = int(self.mask_resolution * 1.6)
            mask_h = int(mask_w * content_h / max(1, content_w))
        else:
            mask_h = int(self.mask_resolution * 1.6)
            mask_w = int(mask_h * content_w / max(1, content_h))
        mask_w = max(32, mask_w)
        mask_h = max(32, mask_h)
        px_per_mask_x = content_w / mask_w
        px_per_mask_y = content_h / mask_h

        occupancy = np.zeros((mask_h, mask_w), dtype=bool)

        placements: list[PlacedItem] = []
        pixels_checked_total = 0

        for s in species_sorted:
            if s.slug not in draws:
                continue
            dw, dh = draws[s.slug]
            alpha_cropped = alpha_crops[s.slug]

            # Resize the alpha crop to fit within mask coordinates
            # proportional to this species' draw size.
            sm_w = max(2, int(round(dw / px_per_mask_x)))
            sm_h = max(2, int(round(dh / px_per_mask_y)))
            # Clamp to occupancy size.
            sm_w = min(sm_w, mask_w)
            sm_h = min(sm_h, mask_h)

            mask_small = alpha_cropped.resize(
                (sm_w, sm_h), Image.LANCZOS
            )
            target_mask = np.array(mask_small) > 64
            species_pixels = int(target_mask.sum())
            if species_pixels <= 0:
                continue

            # Walk candidate positions with coarse step.
            step = max(2, sm_w // 24)
            cx_mask = mask_w // 2
            cy_mask = mask_h // 2

            def _find_position(tolerance: float) -> tuple[int, int, int] | None:
                nonlocal pixels_checked_total
                best: tuple[float, int, int, int] | None = None
                y = 0
                while y <= mask_h - sm_h:
                    x = 0
                    while x <= mask_w - sm_w:
                        region = occupancy[y:y + sm_h, x:x + sm_w]
                        overlap_count = int(np.logical_and(target_mask, region).sum())
                        pixels_checked_total += target_mask.size
                        frac = overlap_count / species_pixels
                        if frac <= tolerance:
                            # center of placed bbox
                            px_center = x + sm_w / 2
                            py_center = y + sm_h / 2
                            dist = (px_center - cx_mask) ** 2 + (
                                py_center - cy_mask
                            ) ** 2
                            cand = (dist, overlap_count, x, y)
                            if best is None or cand < best:
                                best = cand
                        x += step
                    y += step
                if best is None:
                    return None
                return best[2], best[3], best[1]

            # Try increasingly relaxed tolerances. First try ZERO overlap —
            # only allow overlap when there's no clean position available.
            pos = _find_position(0.0)
            if pos is None:
                pos = _find_position(0.02)
            if pos is None:
                pos = _find_position(0.08)
            if pos is None:
                pos = _find_position(0.20)
                if pos is not None:
                    warnings.append(
                        f"'{s.slug}': placed with up to 20% overlap (canvas crowded)."
                    )
            if pos is None:
                warnings.append(
                    f"SilhouettePackedLayoutEngine: no valid position for "
                    f"'{s.slug}' — placing at top-left fallback."
                )
                pos = (0, 0, 0)

            mx, my, _ov = pos
            # Update occupancy.
            occupancy[my:my + sm_h, mx:mx + sm_w] |= target_mask

            # Convert mask coords to canvas pixels.
            canvas_x = int(round(content_x0 + mx * px_per_mask_x))
            canvas_y = int(round(content_y0 + my * px_per_mask_y))

            placements.append(
                PlacedItem(
                    species_ref=s,
                    master=masters[s.slug],
                    x=canvas_x,
                    y=canvas_y,
                    draw_width=dw,
                    draw_height=dh,
                )
            )

        logger.info(
            "SilhouettePackedLayoutEngine: placed %d species; mask=%dx%d; "
            "alpha pixels evaluated ~%d",
            len(placements), mask_w, mask_h, pixels_checked_total,
        )

        return LayoutResult(
            poster=spec,
            placements=placements,
            warnings=warnings,
        )
