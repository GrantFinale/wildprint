"""Pixel-mask preprocessing for irregular-shape poster packing.

Used by :class:`FieldGuidePackedEngine` (engines_v3.py). The packer needs
each fish's true silhouette (alpha mask) plus its label rectangle plus a
buffer (``buff1``) inflation, all as a single binary mask on a downsampled
pack grid. This module builds those masks, caches them across renders, and
exposes a tiny API the engine can call.

Diagram — anatomy of a pack mask
================================

    ┌──────── composite (alpha + label + 2*buff1) ─────────┐
    │  buff1 padding (top)                                 │
    │  ┌──── fish alpha silhouette (irregular) ──────┐     │
    │  │             ▲                                │     │
    │  │            ╱ ╲                               │     │
    │  │     ▄▄▄▄▄▀   ▀▄▄▄▄                          │     │
    │  │    ▌  ●     ███▄▄  ◀── fish bbox            │     │
    │  │     ▀▀▀▀▀▀▀▀▀▀▀▀                            │     │
    │  └──────────────────────────────────────────────┘     │
    │  buff1 padding (between fish and label)              │
    │  ┌──── label rect (~0.8 * fish_w wide) ────────┐     │
    │  │ NORTHERN PIKE                               │     │
    │  │ Esox lucius                                 │     │
    │  └──────────────────────────────────────────────┘     │
    │  buff1 padding (bottom)                              │
    └──────────────────────────────────────────────────────┘

After ``scipy.ndimage.binary_dilation`` by ``buff1`` pixels, the boundary
inflates outward by buff1 — giving the packer a single binary footprint that
already encodes "fish + label + buffer all in one go." Two such masks that
don't overlap mathematically can be placed adjacent on the canvas with the
desired buff1 of space around each.

Caches
======

Two-tier:
- Per-render base cache: build the mask ONCE at scale=1.0 per render,
  zoom to current scale per iteration. Cuts ~80% of build cost when the
  binary search probes many scales.
- Cross-render LRU cache (``_MASK_CACHE``): keyed on
  ``(image_path, mask_resolution, target_w_base)``. Capped at
  ``_MASK_CACHE_MAX`` entries (~50MB at default sizes). Hit rate is high
  when the same lake re-renders (preview iteration in the UI).
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, zoom

# Cap the cross-render cache so it can't grow without bound. Each mask is
# small (a few KB at mask_resolution=8 for a typical fish), but keys are
# cheap to evict and we never want this in the way of a long-running
# server process.
_MASK_CACHE_MAX = 256
_MASK_CACHE: OrderedDict[tuple[str, int, int], np.ndarray] = OrderedDict()


def downsample_mask(mask: np.ndarray, factor: int) -> np.ndarray:
    """Downsample a 2D boolean mask by an integer factor using max-pooling.

    Max-pool (logical OR over each NxN block) is the right semantic for
    binary masks — a block that contains ANY True pixel is True in the
    downsampled mask. This preserves silhouette topology much better than
    nearest-neighbor decimation, which would randomly drop edge pixels.

    Args:
        mask: 2D boolean array.
        factor: Integer downsample factor (>= 1).

    Returns:
        A 2D boolean array of shape (H // factor, W // factor).

    Raises:
        ValueError: If factor < 1 or mask is not 2D.
    """
    if factor < 1:
        raise ValueError(f"factor must be >= 1, got {factor}")
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2D, got shape {mask.shape}")
    if factor == 1:
        return mask.astype(bool)
    h, w = mask.shape
    new_h = h // factor
    new_w = w // factor
    # Trim to a clean multiple of factor.
    trimmed = mask[: new_h * factor, : new_w * factor]
    # Reshape into (new_h, factor, new_w, factor) and OR over the factor
    # axes — this is "max-pooling" for boolean arrays.
    reshaped = trimmed.reshape(new_h, factor, new_w, factor)
    return reshaped.any(axis=(1, 3))


def build_pack_mask(
    image_path: Path,
    target_w_base: int,
    buff1_px: int,
    label_h_px: int,
    label_width_fraction: float = 0.8,
) -> np.ndarray:
    """Build a binary pack mask for one fish at "base scale" (scale=1.0).

    The returned mask is a single 2D boolean array containing:
    1. The fish alpha silhouette (downsampled to ``target_w_base`` wide)
    2. A rectangular label region directly below the fish
    3. ``buff1_px`` of dilation around the whole composite

    The mask is at the **pack grid resolution** — i.e., the values are
    already in "pack pixels," not canvas pixels. The engine zooms this
    mask by the current global scale during binary search.

    Args:
        image_path: Path to the cropped master PNG.
        target_w_base: Width of the fish (in pack pixels) at scale=1.0.
            Height is derived from the master's bbox aspect ratio.
        buff1_px: Buffer thickness in pack pixels (dilation iterations).
        label_h_px: Label rect height in pack pixels.
        label_width_fraction: Label width as a fraction of fish width.
            Default 0.8 (label is slightly narrower than the fish).

    Returns:
        A 2D boolean numpy array. ``True`` where the fish/label/buffer
        footprint lives, ``False`` elsewhere.

    Notes
    -----
    Sentinel behavior: if the master file is entirely transparent (no
    alpha > 0 anywhere), we fall back to a simple rectangle the same size
    as the requested fish bbox plus the label, plus dilation. This keeps
    the packer from crashing on degenerate input — the rectangle is what
    the original cropping would have produced anyway.
    """
    if target_w_base < 1:
        raise ValueError(f"target_w_base must be >= 1, got {target_w_base}")
    if buff1_px < 0:
        raise ValueError(f"buff1_px must be >= 0, got {buff1_px}")
    if label_h_px < 0:
        raise ValueError(f"label_h_px must be >= 0, got {label_h_px}")

    # Load alpha from the master PNG.
    with Image.open(image_path) as img:
        rgba = img.convert("RGBA")
        alpha = np.array(rgba.getchannel("A")) > 30  # >30 matches v2 alpha bbox

    if not alpha.any():
        # Degenerate input: build a sentinel rectangle so the packer can
        # still proceed. Pick a square aspect since we have no fish shape
        # to derive from.
        fish_h = max(1, target_w_base // 2)
        fish = np.ones((fish_h, target_w_base), dtype=bool)
    else:
        # Tight-crop to alpha bbox (mirrors the v2 alpha_bbox logic) so
        # we don't waste pack-grid pixels on transparent padding.
        ys, xs = np.where(alpha)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        alpha_crop = alpha[y0:y1, x0:x1]
        # Resize crop to (target_w_base, target_h_base) preserving aspect.
        crop_h, crop_w = alpha_crop.shape
        aspect = crop_w / crop_h
        target_h_base = max(1, int(round(target_w_base / aspect)))
        zh = target_h_base / crop_h
        zw = target_w_base / crop_w
        # nearest-neighbor for binary masks (order=0 in scipy.ndimage.zoom)
        fish = zoom(alpha_crop.astype(np.uint8), (zh, zw), order=0).astype(bool)

    fh, fw = fish.shape
    label_w = max(1, int(round(label_width_fraction * fw)))
    composite_h = fh + label_h_px + 2 * buff1_px
    composite_w = fw + 2 * buff1_px
    composite = np.zeros((composite_h, composite_w), dtype=bool)
    # Place fish in the composite, with buff1 padding on top and sides.
    composite[buff1_px : buff1_px + fh, buff1_px : buff1_px + fw] = fish
    # Place label rect immediately below the fish, horizontally centered.
    if label_h_px > 0:
        lx_start = (composite_w - label_w) // 2
        composite[
            buff1_px + fh : buff1_px + fh + label_h_px,
            lx_start : lx_start + label_w,
        ] = True
    # Dilate by buff1 — inflates the boundary outward by buff1_px pixels.
    if buff1_px > 0:
        composite = binary_dilation(composite, iterations=buff1_px)
    return composite


def get_or_build_pack_mask(
    image_path: Path,
    target_w_base: int,
    buff1_px: int,
    label_h_px: int,
) -> np.ndarray:
    """Cached version of :func:`build_pack_mask`.

    Keyed on (image_path, target_w_base, buff1_px+label_h_px). Hit rate
    is high when the same species renders across multiple poster previews
    or A/B comparisons. LRU-evicts when ``_MASK_CACHE_MAX`` is exceeded.
    """
    # Composite key — buff1+label uniquely identify the mask shape given
    # the master + target width (the canvas-resolution-dependent values).
    key = (str(image_path), int(target_w_base), int(buff1_px) * 10000 + int(label_h_px))
    cached = _MASK_CACHE.get(key)
    if cached is not None:
        # Move to end (LRU).
        _MASK_CACHE.move_to_end(key)
        return cached
    mask = build_pack_mask(image_path, target_w_base, buff1_px, label_h_px)
    _MASK_CACHE[key] = mask
    if len(_MASK_CACHE) > _MASK_CACHE_MAX:
        _MASK_CACHE.popitem(last=False)  # evict oldest
    return mask


def scale_pack_mask(base_mask: np.ndarray, scale: float) -> np.ndarray:
    """Zoom a base mask (scale=1.0) to a new scale.

    Uses nearest-neighbor zoom (order=0) which is monotonic in ``scale``
    — critical for the engine's binary search. Float zoom rather than
    rebuild-from-dilation, so the mask shape changes smoothly with
    ``scale`` and there's no integer-rounding flicker near boundaries.

    Args:
        base_mask: 2D boolean mask at scale=1.0.
        scale: Float scale (>0).

    Returns:
        2D boolean mask at the new scale. Returns the input unchanged
        when scale is very close to 1.0.
    """
    if scale <= 0:
        raise ValueError(f"scale must be > 0, got {scale}")
    if abs(scale - 1.0) < 1e-6:
        return base_mask
    return zoom(base_mask.astype(np.uint8), scale, order=0).astype(bool)


def clear_cache() -> None:
    """Drop all cached masks. Useful in tests and after master regeneration."""
    _MASK_CACHE.clear()


def cache_size() -> int:
    """Return the current number of cached masks (for diagnostics)."""
    return len(_MASK_CACHE)


def validate_no_silhouette_overlap(
    placements: list,
    *,
    buff1_canvas_px: int,
    label_h_canvas_px: int,
    mask_resolution: int = 8,
) -> list[tuple[str, str, int]]:
    """Verify no two placed fish silhouettes overlap (alpha-mask precision).

    v2's stack_fits enforces a label_reserve y-gap between x-overlapping
    bboxes. That uses the rectangular bbox, not the actual fish
    silhouette. In edge cases a thin tail or fin extending into another
    fish's bbox area could collide with that fish's silhouette even when
    bboxes are separated by the bbox-level gap.

    This validator catches those cases:
    1. Build a downsampled binary mask (alpha + dilate by buff1) for
       each placement at its actual rendered scale.
    2. OR the masks onto a shared canvas at their respective positions.
    3. Walk pairwise — if any TWO masks have overlapping pixels, the
       pair has a silhouette collision.

    Returns a list of ``(slug_a, slug_b, overlap_pixel_count)`` for each
    colliding pair. Empty list = clean. Caller can log this as a warning
    or raise; this function does not gate.

    Args:
        placements: List of PlacedItem (or anything with ``.species_ref.slug``,
            ``.master.image_path``, ``.x``, ``.y``, ``.draw_width``,
            ``.draw_height``).
        buff1_canvas_px: Buffer thickness in CANVAS pixels.
        label_h_canvas_px: Label rect height in CANVAS pixels.
        mask_resolution: Pack-grid downsample factor (default 8).
    """
    if not placements:
        return []

    res = max(1, int(mask_resolution))
    buff1_pack = max(1, buff1_canvas_px // res)
    label_pack = max(1, label_h_canvas_px // res)

    # Build per-placement (mask, x_pack, y_pack) tuples. y is the FISH
    # top, so we shift up by buff1 to reach the dilated mask's origin.
    items: list[tuple[str, np.ndarray, int, int]] = []
    for p in placements:
        # Render-time draw width: convert to pack pixels.
        target_w_pack = max(2, p.draw_width // res)
        try:
            mask = get_or_build_pack_mask(
                p.master.image_path,
                target_w_base=target_w_pack,
                buff1_px=buff1_pack,
                label_h_px=label_pack,
            )
        except Exception:
            # Defensive: skip placements whose master can't be loaded.
            continue
        # Origin in pack grid: (x - buff1, y - buff1) since the mask
        # has buff1 padding around the fish.
        x_pack = p.x // res - buff1_pack
        y_pack = p.y // res - buff1_pack
        items.append((p.species_ref.slug, mask, x_pack, y_pack))

    # Pairwise check via numpy AND on the overlap region.
    collisions: list[tuple[str, str, int]] = []
    for i in range(len(items)):
        slug_a, mask_a, ax, ay = items[i]
        ah, aw = mask_a.shape
        for j in range(i + 1, len(items)):
            slug_b, mask_b, bx, by = items[j]
            bh, bw = mask_b.shape
            # Compute overlap rectangle in pack grid.
            ox0 = max(ax, bx)
            oy0 = max(ay, by)
            ox1 = min(ax + aw, bx + bw)
            oy1 = min(ay + ah, by + bh)
            if ox1 <= ox0 or oy1 <= oy0:
                continue  # bboxes don't even overlap — silhouettes can't
            # AND the two masks' overlap region.
            a_slice = mask_a[oy0 - ay : oy1 - ay, ox0 - ax : ox1 - ax]
            b_slice = mask_b[oy0 - by : oy1 - by, ox0 - bx : ox1 - bx]
            overlap_count = int((a_slice & b_slice).sum())
            if overlap_count > 0:
                collisions.append((slug_a, slug_b, overlap_count))
    return collisions


__all__ = [
    "build_pack_mask",
    "get_or_build_pack_mask",
    "scale_pack_mask",
    "downsample_mask",
    "validate_no_silhouette_overlap",
    "clear_cache",
    "cache_size",
]
