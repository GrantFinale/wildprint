"""Concept-2 layout engine: irregular-shape pixel-mask packing.

This is a from-scratch alternative to :class:`FieldGuideBandsEngine`
(v2). v2 uses a row/pair/single grid structure. v3 abandons rows entirely
and packs every fish as an irregular pixel-mask footprint (alpha + label
+ buff1 buffer) into the body region.

When to use which
=================

- ``layout_style="field_guide"`` → v2 (FieldGuideBandsEngine on main)
- ``layout_style="field_guide_packed"`` → v3 (this engine)

v2 produces a more "field-guide-textbook" look (alternating L/R pairs
with centered staggers). v3 nests by silhouette and can fit more fish in
the same vertical space when the species mix has compatible shapes.

Algorithm
=========

::

    layout(spec, species, loader)
        │
        ▼
    ┌───────────────────────────────────────────────────┐
    │ 1. Carve regions (pack grid)                      │
    │      title_band, body_top/bot, side_margins       │
    │      buff1 = label font height (in pack pixels)   │
    └───────────────────────────────────────────────────┘
        │
        ▼
    ┌───────────────────────────────────────────────────┐
    │ 2. Identify hero (max relative_scale_index)       │
    │    Hero is pinned top-center, hero1× others       │
    └───────────────────────────────────────────────────┘
        │
        ▼
    ┌───────────────────────────────────────────────────┐
    │ 3. Recovery loop (only fires on infeasibility):   │
    │    for hero1 in [1.5, 1.4, ..., 1.0]:             │
    │        if best_pack(hero1) succeeds: return        │
    │    while not feasible:                             │
    │        drop last-selected fish, reset hero1=1.5,   │
    │        retry. Append to excluded_species list.     │
    └───────────────────────────────────────────────────┘
        │
        ▼
    ┌───────────────────────────────────────────────────┐
    │ 4. best_pack(hero1, fish_set):                     │
    │      for ordering in [size_desc, water_column,    │
    │                       aspect_desc, seeded_shuffle]: │
    │          scale = binary_search(ordering, hero1)   │
    │          candidate_layouts.append((scale, ...))   │
    │      pick max-scale layout. Tiebreak: lower CV    │
    │      of inter-fish gaps.                          │
    └───────────────────────────────────────────────────┘
        │
        ▼
    ┌───────────────────────────────────────────────────┐
    │ 5. Emit PlacedItem in canvas (full-resolution)    │
    │    coordinates by scaling pack-grid coords by     │
    │    mask_resolution.                               │
    └───────────────────────────────────────────────────┘

Buffer concept ("buff1")
========================

The user specified a single uniform buffer ``buff1`` equal to the label
font height. Used as:
- Inset between the inner border line and the top fish
- Inset between the title and the top fish
- Inset between the bottom fish/label and the inner border
- Dilation around every fish + label

This means every fish has buff1 of clear space on all sides, including
against the canvas boundaries, and labels never crash into each other.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict

import numpy as np

from poster_layout import masks, skyline_packer
from poster_layout.engines import _resolve_species_with_masters
from poster_layout.interfaces import LayoutEngine, MasterImageLoader
from poster_layout.models import (
    LayoutResult,
    MasterImage,
    PlacedItem,
    PosterSpec,
    SpeciesRef,
)


# Mirrors v2's MIN_IDX_FLOOR semantics: very-small idx species don't render
# absurdly tiny — we floor before compression.
_MIN_IDX_FLOOR = 0.4


def _compressed_idx(ref: SpeciesRef, compression: float) -> float:
    """Compress a species idx the same way v2 does."""
    raw = max(_MIN_IDX_FLOOR, float(ref.relative_scale_index))
    return raw ** compression


def _spec_seed(spec: PosterSpec, salt: int = 0) -> int:
    """Deterministic int seed derived from the poster spec + a salt.

    Used by the seeded-shuffle ordering so renders are reproducible:
    same spec hash + same salt → same shuffle → same poster output.
    """
    key = (
        spec.title
        + "|"
        + spec.style_slug
        + "|"
        + ",".join(spec.species_slugs)
        + "|"
        + str(salt)
    )
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


class FieldGuidePackedEngine(LayoutEngine):
    """Irregular-shape pixel-mask packer for field-guide posters.

    See module docstring for the full algorithm. Tunables are passed to
    ``__init__`` as kwargs.
    """

    def __init__(
        self,
        *,
        hero1: float = 1.5,
        run1: int = 4,
        buff1_fraction: float = 0.026,
        mask_resolution: int = 8,
        title_band_fraction: float = 0.20,
        bottom_margin_fraction: float = 0.045,
        side_margin_fraction: float = 0.060,
        scale_compression: float = 0.30,
        min_scale: float = 0.10,
        max_scale: float = 1.0,
        binary_search_iters: int = 12,
        recovery_hero1_steps: int = 6,
    ) -> None:
        """Construct a FieldGuidePackedEngine.

        Args:
            hero1: Hero size multiplier (default 1.5 = 150% of others).
            run1: Number of distinct placement orderings to try (default 4).
            buff1_fraction: Buffer thickness as a fraction of canvas_h.
                Default 0.026 matches the label font height.
            mask_resolution: Pack-grid downsample factor (default 8).
                Smaller = finer nesting + slower. 8 is the validated
                viable value from the perf spike.
            title_band_fraction: Top fraction of canvas reserved for title.
            bottom_margin_fraction: Bottom fraction (outer + buff1) reserved.
            side_margin_fraction: Left+right fraction for side buffers.
            scale_compression: Idx exponent (matches v2).
            min_scale, max_scale: Binary search bounds for global scale.
            binary_search_iters: Iterations per ordering (~1e-4 precision).
            recovery_hero1_steps: How many hero1 values to try before
                dropping fish in the recovery loop.
        """
        self.hero1 = max(1.0, float(hero1))
        self.run1 = max(1, int(run1))
        self.buff1_fraction = float(buff1_fraction)
        self.mask_resolution = max(1, int(mask_resolution))
        self.title_band_fraction = float(title_band_fraction)
        self.bottom_margin_fraction = float(bottom_margin_fraction)
        self.side_margin_fraction = float(side_margin_fraction)
        self.scale_compression = max(0.05, min(1.0, float(scale_compression)))
        self.min_scale = float(min_scale)
        self.max_scale = float(max_scale)
        self.binary_search_iters = max(4, int(binary_search_iters))
        self.recovery_hero1_steps = max(1, int(recovery_hero1_steps))

    # ------------------------------------------------------------------
    # LayoutEngine protocol
    # ------------------------------------------------------------------

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        warnings: list[str] = []
        excluded: list[str] = []

        if not species:
            warnings.append("FieldGuidePackedEngine received zero species.")
            return LayoutResult(
                poster=spec, placements=[], warnings=warnings, excluded_species=excluded
            )

        pairs = _resolve_species_with_masters(spec, species, loader, warnings)
        if not pairs:
            warnings.append("FieldGuidePackedEngine: no masters available.")
            return LayoutResult(
                poster=spec, placements=[], warnings=warnings, excluded_species=excluded
            )

        # Carve canvas regions in PACK GRID coordinates. mask_resolution
        # divides every canvas dimension; final placements scale back up.
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        res = self.mask_resolution
        pack_w = max(1, canvas_w // res)
        pack_h = max(1, canvas_h // res)
        buff1_px = max(1, int(round(canvas_h * self.buff1_fraction)) // res)
        label_h_px = buff1_px  # label font height == buff1 (user-defined)
        side_pack = int(round(canvas_w * self.side_margin_fraction)) // res
        body_top_pack = int(round(canvas_h * self.title_band_fraction)) // res
        body_bot_pack = pack_h - int(round(canvas_h * self.bottom_margin_fraction)) // res
        # Stash for _emit_placements (avoids passing through every method).
        self._last_buff1_px = buff1_px
        self._last_label_h_px = label_h_px
        self._last_res = res

        # Reduce body_top by buff1 to add the buff1 inset between title
        # and top fish (per user spec: "buff1 also = distance under the
        # title to the top of the buffer of the top fish"). We add buff1
        # below body_top by shifting the effective top down.
        # NOTE: the masks ALREADY have buff1 padding baked in, so we
        # don't double-count. body_top_pack stays at the title boundary.

        # Recovery loop: try hero1 [self.hero1 .. 1.0], then drop fish.
        remaining = list(pairs)
        for _ in range(len(pairs)):
            result = self._try_pack(
                spec=spec,
                pairs=remaining,
                pack_w=pack_w,
                pack_h=pack_h,
                buff1_px=buff1_px,
                label_h_px=label_h_px,
                side_pack=side_pack,
                body_top_pack=body_top_pack,
                body_bot_pack=body_bot_pack,
            )
            if result is not None:
                hero1_used, placements_pack = result
                if hero1_used < self.hero1 - 1e-3:
                    warnings.append(
                        f"FieldGuidePackedEngine: hero1 shrunk from "
                        f"{self.hero1:.2f} to {hero1_used:.2f} to fit."
                    )
                placements = self._emit_placements(remaining, placements_pack, res)
                if excluded:
                    warnings.append(
                        f"FieldGuidePackedEngine: dropped {len(excluded)} "
                        f"species to fit: {', '.join(excluded)}"
                    )
                return LayoutResult(
                    poster=spec,
                    placements=placements,
                    warnings=warnings,
                    excluded_species=excluded,
                )
            # Pack failed at every hero1. Drop the last-selected species.
            dropped = remaining.pop()
            excluded.append(dropped[0].slug)
            if not remaining:
                break

        # Pack failed even with 1 fish remaining → genuinely infeasible.
        warnings.append(
            "FieldGuidePackedEngine: layout infeasible — could not place any fish."
        )
        return LayoutResult(
            poster=spec, placements=[], warnings=warnings, excluded_species=excluded
        )

    # ------------------------------------------------------------------
    # Recovery layer: try multiple hero1 values
    # ------------------------------------------------------------------

    def _try_pack(
        self,
        *,
        spec: PosterSpec,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        pack_w: int,
        pack_h: int,
        buff1_px: int,
        label_h_px: int,
        side_pack: int,
        body_top_pack: int,
        body_bot_pack: int,
    ) -> tuple[float, list[tuple[float, int, int, int, int]]] | None:
        """Run the K-ordering search at decreasing hero1 until one succeeds.

        Returns ``(hero1_used, placements_pack)`` on success where
        ``placements_pack`` is parallel to ``pairs`` with tuples of
        ``(scale_used, draw_w_pack, draw_h_pack, x_pack, y_pack)``.
        Returns None if no hero1 in the recovery sequence produces a
        feasible layout.
        """
        if not pairs:
            return None

        # Identify hero = max raw idx (matches v2).
        hero_idx = max(range(len(pairs)), key=lambda i: pairs[i][0].relative_scale_index)

        # hero1 trajectory: full hero1 → 1.0 (descending).
        hero1_values = self._hero1_trajectory()
        for hero1 in hero1_values:
            best = self._best_pack(
                spec=spec,
                pairs=pairs,
                hero_idx=hero_idx,
                hero1=hero1,
                pack_w=pack_w,
                pack_h=pack_h,
                buff1_px=buff1_px,
                label_h_px=label_h_px,
                side_pack=side_pack,
                body_top_pack=body_top_pack,
                body_bot_pack=body_bot_pack,
            )
            if best is not None:
                return (hero1, best)
        return None

    def _hero1_trajectory(self) -> list[float]:
        """Descending hero1 values to try during recovery."""
        if self.hero1 <= 1.0 + 1e-6:
            return [self.hero1]
        n = self.recovery_hero1_steps
        return [
            self.hero1 - (self.hero1 - 1.0) * (i / max(1, n - 1)) for i in range(n)
        ]

    # ------------------------------------------------------------------
    # best_pack: try K orderings, return best-scoring layout
    # ------------------------------------------------------------------

    def _best_pack(
        self,
        *,
        spec: PosterSpec,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        hero_idx: int,
        hero1: float,
        pack_w: int,
        pack_h: int,
        buff1_px: int,
        label_h_px: int,
        side_pack: int,
        body_top_pack: int,
        body_bot_pack: int,
    ) -> list[tuple[float, int, int, int, int]] | None:
        """Try K orderings, return the placements from the highest-scoring one.

        Each ordering is a permutation of indices into ``pairs`` (with
        the hero already at position 0). Binary-search the global scale
        for each ordering and pick the layout with the largest scale.

        Returns None if no ordering produces a feasible layout at any
        scale ≥ min_scale.
        """
        orderings = self._orderings(spec, pairs, hero_idx)
        best_score = float("-inf")
        best_placements: list[tuple[float, int, int, int, int]] | None = None
        for order in orderings:
            scale, placements = self._binary_search_scale(
                pairs=pairs,
                order=order,
                hero1=hero1,
                pack_w=pack_w,
                pack_h=pack_h,
                buff1_px=buff1_px,
                label_h_px=label_h_px,
                side_pack=side_pack,
                body_top_pack=body_top_pack,
                body_bot_pack=body_bot_pack,
            )
            if placements is None:
                continue
            score = self._score_layout(scale, placements, pack_w)
            if score > best_score:
                best_score = score
                best_placements = placements
        return best_placements

    @staticmethod
    def _score_layout(
        scale: float,
        placements: list[tuple[float, int, int, int, int] | None],
        pack_w: int,
    ) -> float:
        """Score a candidate layout for the best-of-K selection.

        Composite of two terms:
        1. Scale (primary) — bigger fish is better.
        2. Horizontal balance (tiebreak) — penalize layouts whose
           total bbox area is heavily skewed left or right of canvas
           centerline. Penalty caps at 20% of the scale, so balance
           never overrides a clearly-larger-scale layout, only
           tiebreaks among near-equal candidates.

        Score = scale * (1 - 0.20 * |imbalance|), where imbalance ∈ [0, 1]
        is the absolute fraction of total bbox area sitting on the heavier
        side of the canvas centerline.

        Mass split uses GEOMETRIC overlap with each half-canvas — a fish
        spanning the centerline contributes proportionally to BOTH sides
        (a perfectly-centered hero is neutral, not arbitrarily L or R).
        """
        center_line = pack_w / 2.0
        left_area = 0.0
        right_area = 0.0
        for pl in placements:
            if pl is None:
                continue
            _scale_used, mw, mh, x, _y = pl
            x_right = x + mw
            # Geometric overlap with each half-canvas.
            left_w = max(0.0, min(center_line, x_right) - x)
            right_w = max(0.0, x_right - max(center_line, float(x)))
            left_area += left_w * mh
            right_area += right_w * mh
        total = left_area + right_area
        imbalance = abs(left_area - right_area) / max(1.0, total)  # [0, 1]
        return scale * (1.0 - 0.20 * imbalance)

    def _orderings(
        self,
        spec: PosterSpec,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        hero_idx: int,
    ) -> list[list[int]]:
        """Generate K=run1 placement orderings. Hero always first.

        Orderings (in order):
        1. size-desc:    sort by -idx, hero first
        2. water-column: top → mid → bottom, hero first
        3. aspect-desc:  widest fish first, hero first
        4. seeded-shuffle: deterministic shuffle of non-hero, hero first

        Returns indices into ``pairs``, each list starting with hero_idx.
        """
        n = len(pairs)
        non_hero = [i for i in range(n) if i != hero_idx]
        WATER_RANK = {"top": 0, "mid": 1, "bottom": 2}

        def by_size_desc(i: int) -> float:
            return -pairs[i][0].relative_scale_index

        def by_water(i: int) -> tuple[int, float]:
            wc = getattr(pairs[i][0], "water_column", "mid")
            return (WATER_RANK.get(wc, 1), -pairs[i][0].relative_scale_index)

        def by_aspect_desc(i: int) -> float:
            m = pairs[i][1]
            return -(m.bbox_width_px / max(1, m.bbox_height_px))

        ords: list[list[int]] = []
        ords.append([hero_idx] + sorted(non_hero, key=by_size_desc))
        if self.run1 >= 2:
            ords.append([hero_idx] + sorted(non_hero, key=by_water))
        if self.run1 >= 3:
            ords.append([hero_idx] + sorted(non_hero, key=by_aspect_desc))
        if self.run1 >= 4:
            import random as _r

            shuffled = list(non_hero)
            _r.Random(_spec_seed(spec, 0)).shuffle(shuffled)
            ords.append([hero_idx] + shuffled)
        # Extra orderings (run1 > 4) — seeded shuffles with different salts.
        for extra in range(5, self.run1 + 1):
            import random as _r

            shuffled = list(non_hero)
            _r.Random(_spec_seed(spec, extra)).shuffle(shuffled)
            ords.append([hero_idx] + shuffled)
        return ords

    # ------------------------------------------------------------------
    # Binary search the global scale for one ordering
    # ------------------------------------------------------------------

    def _binary_search_scale(
        self,
        *,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        order: list[int],
        hero1: float,
        pack_w: int,
        pack_h: int,
        buff1_px: int,
        label_h_px: int,
        side_pack: int,
        body_top_pack: int,
        body_bot_pack: int,
    ) -> tuple[float, list[tuple[float, int, int, int, int]] | None]:
        """Find max scale ``s`` where the ordering packs successfully.

        Returns ``(scale, placements)`` where placements is a list parallel
        to ``pairs`` (NOT the order). Returns ``(0.0, None)`` if no scale
        ≥ min_scale produces a feasible layout.
        """
        # Base sizing: at scale=1.0, the hero takes ~usable_w_pack.
        # Per-fish target_w_base = unit_w * compressed_idx (* hero1 if hero).
        usable_w_pack = max(1, pack_w - 2 * side_pack)
        idxs = [_compressed_idx(p[0], self.scale_compression) for p in pairs]
        hero_pos_in_pairs = order[0]
        hero_compressed = idxs[hero_pos_in_pairs]
        # unit_w_base sized so hero at scale=1.0 takes the full usable_w.
        unit_w_base = usable_w_pack / max(1e-6, hero_compressed * hero1)

        # Per-fish base width at scale=1.0:
        base_widths = [
            int(round(unit_w_base * idxs[i] * (hero1 if i == hero_pos_in_pairs else 1.0)))
            for i in range(len(pairs))
        ]

        def pack_at_scale(s: float) -> list[tuple[float, int, int, int, int]] | None:
            """Build masks at scale s, pack via skyline, return placements."""
            order_widths = [max(2, int(round(base_widths[i] * s))) for i in order]
            order_masks: list[np.ndarray] = []
            for ord_pos, pair_idx in enumerate(order):
                ref, master = pairs[pair_idx]
                w = order_widths[ord_pos]
                # Use base width / mask scale for caching: build at
                # base, scale at iteration.
                # For simplicity (and to leverage masks.scale_pack_mask
                # for monotonic search), build base mask at scale=1.0
                # and zoom by s.
                base_mask = masks.get_or_build_pack_mask(
                    master.image_path,
                    target_w_base=base_widths[pair_idx],
                    buff1_px=buff1_px,
                    label_h_px=label_h_px,
                )
                m = masks.scale_pack_mask(base_mask, s)
                # Guard against degenerate too-small masks
                if m.shape[0] < 2 or m.shape[1] < 2:
                    return None
                order_masks.append(m)

            # Pin hero at top-center.
            hero_mask = order_masks[0]
            hw = hero_mask.shape[1]
            hero_x = max(side_pack, (pack_w - hw) // 2)
            hero_y = body_top_pack
            # Ensure hero fits.
            if hero_x + hw > pack_w - side_pack:
                return None
            if hero_y + hero_mask.shape[0] >= body_bot_pack:
                return None

            # ---- Mass-balancing placement loop ---------------------
            # For each non-hero fish, target the LIGHTER side of the
            # canvas so far. This breaks the systematic right-drift you
            # get when `argmin(|x - center|)` always tie-breaks to the
            # first valid position. Targeting strength scales with the
            # current imbalance (max ±0.25 of canvas width off-center)
            # so a moderately-imbalanced canvas only nudges placements
            # slightly, while a wildly imbalanced one strongly biases
            # the next fish toward the empty side.
            canvas = np.zeros((pack_h, pack_w), dtype=bool)
            skyline_packer.place_mask(canvas, hero_mask, hero_y, hero_x)
            center_line = pack_w // 2

            placements: list[tuple[float, int, int, int, int] | None] = [None] * len(pairs)
            placements[order[0]] = (s, hw, hero_mask.shape[0], hero_x, hero_y)

            for k, pair_idx in enumerate(order[1:], start=1):
                m = order_masks[k]
                # Mass on each half of the canvas right now.
                left_mass = int(canvas[:, :center_line].sum())
                right_mass = int(canvas[:, center_line:].sum())
                total = left_mass + right_mass
                # imbalance ∈ [-1, 1]: positive = right-heavy.
                imbalance = (right_mass - left_mass) / max(1, total)
                # Push the target away from the heavy side, ±25% of canvas.
                target_center_x = int(pack_w * (0.5 - 0.25 * imbalance))
                pos = skyline_packer.find_placement(
                    canvas,
                    m,
                    body_top=body_top_pack,
                    body_bot=body_bot_pack,
                    side_left=side_pack,
                    side_right=pack_w - side_pack,
                    center_x=target_center_x,
                )
                if pos is None:
                    return None
                y, x = pos
                skyline_packer.place_mask(canvas, m, y, x)
                mh, mw = m.shape
                placements[pair_idx] = (s, mw, mh, x, y)
            return placements  # type: ignore[return-value]

        # Binary search [min_scale, max_scale].
        if pack_at_scale(self.max_scale) is not None:
            # Already at max — no need to search.
            best = pack_at_scale(self.max_scale)
            return (self.max_scale, best)
        lo = self.min_scale
        hi = self.max_scale
        best_layout: list[tuple[float, int, int, int, int]] | None = None
        best_scale = 0.0
        # Check the bottom of the range first — if even min_scale fails,
        # this hero1 / fish set is infeasible.
        lo_layout = pack_at_scale(lo)
        if lo_layout is None:
            return (0.0, None)
        best_layout, best_scale = lo_layout, lo

        for _ in range(self.binary_search_iters):
            mid = (lo + hi) / 2.0
            layout = pack_at_scale(mid)
            if layout is not None:
                best_layout, best_scale = layout, mid
                lo = mid
            else:
                hi = mid

        return (best_scale, best_layout)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    def _emit_placements(
        self,
        pairs: list[tuple[SpeciesRef, MasterImage]],
        placements_pack: list[tuple[float, int, int, int, int]],
        res: int,
    ) -> list[PlacedItem]:
        """Convert pack-grid placements to full-canvas-resolution PlacedItems.

        The mask in the pack grid includes buff1 padding on all sides
        plus a label rect below the fish. The actual fish illustration
        starts at ``pack_origin + buff1``, and is the mask's width minus
        2*buff1. Height is mask_h - 2*buff1 - label_h.
        """
        buff1_px = self._last_buff1_px
        label_h_px = self._last_label_h_px
        out: list[PlacedItem] = []
        for (ref, master), pl in zip(pairs, placements_pack):
            _scale, mask_w, mask_h, x_pack, y_pack = pl
            fish_w_pack = max(1, mask_w - 2 * buff1_px)
            fish_h_pack = max(1, mask_h - 2 * buff1_px - label_h_px)
            fish_x_pack = x_pack + buff1_px
            fish_y_pack = y_pack + buff1_px
            out.append(
                PlacedItem(
                    species_ref=ref,
                    master=master,
                    x=int(fish_x_pack * res),
                    y=int(fish_y_pack * res),
                    draw_width=int(fish_w_pack * res),
                    draw_height=int(fish_h_pack * res),
                )
            )
        return out

    # Set in layout() so _emit_placements can re-derive buff1/label.
    _last_buff1_px: int = 0
    _last_label_h_px: int = 0
    _last_res: int = 1


__all__ = ["FieldGuidePackedEngine"]
