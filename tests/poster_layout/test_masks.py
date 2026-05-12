"""Unit tests for :mod:`poster_layout.masks`.

These tests use synthetic PNGs (no real masters) so they run fast and
don't depend on the master generation pipeline. Only ``build_pack_mask``
needs disk I/O; everything else is pure numpy/scipy.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from poster_layout import masks


# ---- Helpers ---------------------------------------------------------------


def _write_synthetic_master(
    tmp_path: Path,
    name: str,
    width: int,
    height: int,
    shape: str = "ellipse",
) -> Path:
    """Write a synthetic master PNG with a known alpha silhouette.

    Args:
        tmp_path: Pytest tmp_path fixture.
        name: Filename stem.
        width, height: Master pixel dimensions.
        shape: "ellipse" (centered, 80% size), "rect" (centered, 80%),
            or "transparent" (fully transparent).

    Returns:
        Path to the written PNG.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if shape == "transparent":
        pass  # leave fully transparent
    else:
        # Centered, 80% size silhouette
        arr = np.array(img)
        cx, cy = width // 2, height // 2
        rx, ry = int(width * 0.4), int(height * 0.4)
        if shape == "ellipse":
            yy, xx = np.ogrid[:height, :width]
            mask = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1
        elif shape == "rect":
            mask = np.zeros((height, width), dtype=bool)
            mask[cy - ry : cy + ry, cx - rx : cx + rx] = True
        else:
            raise ValueError(f"unknown shape {shape}")
        arr[mask, 3] = 255
        arr[mask, :3] = 128  # mid-gray fill (color doesn't matter)
        img = Image.fromarray(arr)
    path = tmp_path / f"{name}.png"
    img.save(path)
    return path


# ---- downsample_mask -------------------------------------------------------


def test_downsample_mask_factor_1_is_identity() -> None:
    m = np.array([[True, False], [False, True]])
    out = masks.downsample_mask(m, 1)
    assert np.array_equal(out, m)


def test_downsample_mask_max_pool_preserves_topology() -> None:
    """A single True pixel survives downsampling — max-pool means topology
    is preserved (no random pixel drops the way nearest-neighbor would)."""
    m = np.zeros((8, 8), dtype=bool)
    m[3, 3] = True  # one True pixel in the middle
    out = masks.downsample_mask(m, 4)
    # 8//4=2, so output is 2x2. The True pixel at (3,3) falls in block (0,0).
    assert out.shape == (2, 2)
    assert out[0, 0] is np.True_ or out[0, 0] == True
    assert out.sum() == 1


def test_downsample_mask_rejects_invalid_factor() -> None:
    m = np.zeros((4, 4), dtype=bool)
    with pytest.raises(ValueError):
        masks.downsample_mask(m, 0)
    with pytest.raises(ValueError):
        masks.downsample_mask(m, -1)


def test_downsample_mask_rejects_non_2d() -> None:
    m = np.zeros((4, 4, 3), dtype=bool)
    with pytest.raises(ValueError):
        masks.downsample_mask(m, 2)


# ---- build_pack_mask -------------------------------------------------------


def test_build_pack_mask_basic_ellipse(tmp_path: Path) -> None:
    """Ellipse master → pack mask has fish + label + buff1 padding."""
    src = _write_synthetic_master(tmp_path, "fish", 200, 80, shape="ellipse")
    m = masks.build_pack_mask(src, target_w_base=100, buff1_px=10, label_h_px=20)
    # Expected composite size: fh = 100 / aspect = 100 / (200/80) = 40
    # composite_h = 40 + 20 + 2*10 = 80, composite_w = 100 + 2*10 = 120
    # After dilation by 10, total grows by ~10 px on each side.
    # We don't assert exact shape (dilation can grow asymmetrically) but
    # do assert it's >= composite size and contains True pixels.
    assert m.dtype == bool
    assert m.shape[0] >= 80
    assert m.shape[1] >= 120
    assert m.sum() > 0


def test_build_pack_mask_label_appears_below_fish(tmp_path: Path) -> None:
    """The label rect ends up below the fish region (the dilated mask has
    a wider footprint at the bottom than just the fish would)."""
    src = _write_synthetic_master(tmp_path, "fish", 100, 100, shape="ellipse")
    no_label = masks.build_pack_mask(src, target_w_base=80, buff1_px=0, label_h_px=0)
    with_label = masks.build_pack_mask(src, target_w_base=80, buff1_px=0, label_h_px=30)
    # With label, the mask is taller.
    assert with_label.shape[0] > no_label.shape[0]
    # The added rows are at the bottom (label is below the fish).
    extra_rows = with_label.shape[0] - no_label.shape[0]
    assert extra_rows == 30  # label_h_px


def test_build_pack_mask_buff1_dilation_grows_footprint(tmp_path: Path) -> None:
    """Increasing buff1_px increases the True-pixel count strictly."""
    src = _write_synthetic_master(tmp_path, "fish", 100, 100, shape="ellipse")
    m0 = masks.build_pack_mask(src, target_w_base=80, buff1_px=0, label_h_px=0)
    m5 = masks.build_pack_mask(src, target_w_base=80, buff1_px=5, label_h_px=0)
    m10 = masks.build_pack_mask(src, target_w_base=80, buff1_px=10, label_h_px=0)
    assert m0.sum() < m5.sum() < m10.sum()
    # Shape grows by 2*buff1 each axis.
    assert m5.shape[0] == m0.shape[0] + 10
    assert m10.shape[0] == m0.shape[0] + 20


def test_build_pack_mask_different_scales_proportional(tmp_path: Path) -> None:
    """Doubling target_w_base roughly doubles the mask width (within rounding)."""
    src = _write_synthetic_master(tmp_path, "fish", 200, 100, shape="ellipse")
    m_50 = masks.build_pack_mask(src, target_w_base=50, buff1_px=0, label_h_px=0)
    m_100 = masks.build_pack_mask(src, target_w_base=100, buff1_px=0, label_h_px=0)
    # 100/50 = 2.0x, so m_100 should be roughly 2x as wide.
    ratio = m_100.shape[1] / m_50.shape[1]
    assert 1.9 < ratio < 2.1


def test_build_pack_mask_transparent_fallback(tmp_path: Path) -> None:
    """Fully transparent master → sentinel rect mask, no crash."""
    src = _write_synthetic_master(tmp_path, "blank", 100, 100, shape="transparent")
    m = masks.build_pack_mask(src, target_w_base=60, buff1_px=5, label_h_px=10)
    assert m.dtype == bool
    assert m.sum() > 0  # sentinel rect is non-empty
    # Sentinel rect is "fish_h = target_w // 2 = 30" + label 10 + 2*5 buff
    # = 50 high before dilation. After 5px dilation, ~60.
    assert 50 <= m.shape[0] <= 80


def test_build_pack_mask_invalid_target_w(tmp_path: Path) -> None:
    src = _write_synthetic_master(tmp_path, "fish", 100, 100, shape="ellipse")
    with pytest.raises(ValueError):
        masks.build_pack_mask(src, target_w_base=0, buff1_px=5, label_h_px=10)


def test_build_pack_mask_invalid_buff1(tmp_path: Path) -> None:
    src = _write_synthetic_master(tmp_path, "fish", 100, 100, shape="ellipse")
    with pytest.raises(ValueError):
        masks.build_pack_mask(src, target_w_base=50, buff1_px=-1, label_h_px=10)


# ---- get_or_build_pack_mask + caching --------------------------------------


def test_cache_returns_identical_array(tmp_path: Path) -> None:
    """Two calls with same args return the exact same object (cache hit)."""
    masks.clear_cache()
    src = _write_synthetic_master(tmp_path, "fish", 100, 60, shape="ellipse")
    m1 = masks.get_or_build_pack_mask(src, target_w_base=50, buff1_px=5, label_h_px=10)
    m2 = masks.get_or_build_pack_mask(src, target_w_base=50, buff1_px=5, label_h_px=10)
    # Identity check: cache returns the SAME ndarray.
    assert m1 is m2
    assert masks.cache_size() == 1


def test_cache_evicts_when_full(tmp_path: Path) -> None:
    """Cache caps at _MASK_CACHE_MAX. Beyond that, oldest entries are dropped."""
    masks.clear_cache()
    original_max = masks._MASK_CACHE_MAX
    masks._MASK_CACHE_MAX = 3  # shrink for the test
    try:
        srcs = [
            _write_synthetic_master(tmp_path, f"fish_{i}", 80, 50, shape="ellipse")
            for i in range(5)
        ]
        for src in srcs:
            masks.get_or_build_pack_mask(src, target_w_base=40, buff1_px=2, label_h_px=5)
        # Cache should be capped at 3.
        assert masks.cache_size() == 3
    finally:
        masks._MASK_CACHE_MAX = original_max
        masks.clear_cache()


def test_cache_key_distinguishes_buff1(tmp_path: Path) -> None:
    """Same image but different buff1 → distinct cache entries."""
    masks.clear_cache()
    src = _write_synthetic_master(tmp_path, "fish", 100, 60, shape="ellipse")
    m1 = masks.get_or_build_pack_mask(src, target_w_base=50, buff1_px=3, label_h_px=10)
    m2 = masks.get_or_build_pack_mask(src, target_w_base=50, buff1_px=8, label_h_px=10)
    assert m1 is not m2
    assert masks.cache_size() == 2


# ---- scale_pack_mask -------------------------------------------------------


def test_scale_pack_mask_identity_at_1_0() -> None:
    m = np.ones((20, 40), dtype=bool)
    out = masks.scale_pack_mask(m, 1.0)
    assert out is m  # identity short-circuit


def test_scale_pack_mask_half_size() -> None:
    m = np.ones((20, 40), dtype=bool)
    out = masks.scale_pack_mask(m, 0.5)
    assert out.shape == (10, 20)
    assert out.dtype == bool


def test_scale_pack_mask_monotonic_in_scale() -> None:
    """Total True pixels grows monotonically with scale — critical for the
    engine's binary search to converge."""
    m = np.zeros((10, 20), dtype=bool)
    m[3:7, 5:15] = True  # 4x10 = 40 pixels
    counts = [masks.scale_pack_mask(m, s).sum() for s in [0.5, 0.75, 1.0, 1.5, 2.0]]
    # Monotonically non-decreasing as scale grows.
    for i in range(len(counts) - 1):
        assert counts[i] <= counts[i + 1], f"non-monotonic at index {i}: {counts}"


def test_scale_pack_mask_rejects_zero_scale() -> None:
    m = np.ones((10, 10), dtype=bool)
    with pytest.raises(ValueError):
        masks.scale_pack_mask(m, 0.0)
    with pytest.raises(ValueError):
        masks.scale_pack_mask(m, -1.0)
