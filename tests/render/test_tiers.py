"""Tests for :func:`render_tier` + tier configuration."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from review_app.render.renderer import render_tier
from review_app.render.spec import RenderSpec
from review_app.render.tiers import (
    PRINT_CANVAS_HEIGHT,
    PRINT_CANVAS_WIDTH,
    TIER_CONFIG,
    TIER_PREVIEW,
    TIER_PRINT,
    TIER_THUMB,
    get_tier_config,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
def _fake_master_factory(width: int, height: int):
    """Build a deterministic master image — gradient + a few colored stripes.

    Bytes are fully derived from (width, height), so two calls with the same
    dims produce identical output. This makes tier comparisons stable.
    """

    def _factory(spec: RenderSpec, w: int, h: int) -> Image.Image:
        # Gradient from black -> white along the diagonal, then overlay
        # three vertical color stripes for visual structure.
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        x = np.linspace(0, 255, w, dtype=np.uint8)
        y = np.linspace(0, 255, h, dtype=np.uint8)
        gradient = ((x[None, :].astype(int) + y[:, None].astype(int)) // 2).astype(np.uint8)
        arr[..., 0] = gradient
        arr[..., 1] = gradient
        arr[..., 2] = gradient
        # 3 vertical stripes — red, green, blue
        third = w // 3
        arr[: h // 3, 0:third, 0] = 220
        arr[: h // 3, third : 2 * third, 1] = 220
        arr[: h // 3, 2 * third : 3 * third, 2] = 220
        return Image.fromarray(arr, mode="RGB")

    return _factory


@pytest.fixture
def small_master_renderer():
    """Master renderer that produces print-canvas-sized images, but cheaply.

    For tier-3 tests we still need PRINT_CANVAS_WIDTH x PRINT_CANVAS_HEIGHT,
    but for tier-1/2 the renderer produces the same dims and we downscale.

    For unit tests we override the print-canvas constants in test_regression
    where needed; here we use the actual constants but the procedural
    factory is fast (~250 ms).
    """
    return _fake_master_factory(PRINT_CANVAS_WIDTH, PRINT_CANVAS_HEIGHT)


@pytest.fixture
def small_print_master_renderer():
    """Cheaper variant for tests that only need a small print canvas."""
    # Override globally via a smaller canvas so render_tier(tier=3) returns
    # a small PNG and the test stays fast.
    return _fake_master_factory(720, 1080)


@pytest.fixture
def spec() -> RenderSpec:
    return RenderSpec(
        lake="Test Lake",
        species=["bass"],
        art_style="editorial-v1",
        layout_config={"grid": 1},
    )


# ---------------------------------------------------------------------------
# TierConfig sanity
# ---------------------------------------------------------------------------
def test_tier_config_for_each_tier_returns_expected_specs() -> None:
    thumb = get_tier_config(TIER_THUMB)
    assert thumb.long_edge_px == 400
    assert thumb.fmt == "JPEG"
    assert thumb.dpi == 72
    assert thumb.watermark is False
    assert thumb.public is True

    preview = get_tier_config(TIER_PREVIEW)
    assert preview.long_edge_px == 2400
    assert preview.fmt == "JPEG"
    assert preview.dpi == 72
    assert preview.watermark is True
    assert preview.public is True

    print_cfg = get_tier_config(TIER_PRINT)
    assert print_cfg.fmt == "PNG"
    assert print_cfg.dpi == 300
    assert print_cfg.watermark is False
    assert print_cfg.public is False


def test_tier_config_unknown_tier_raises() -> None:
    with pytest.raises(ValueError):
        get_tier_config(99)


def test_tier_config_table_is_complete() -> None:
    assert set(TIER_CONFIG.keys()) == {1, 2, 3}


# ---------------------------------------------------------------------------
# render_tier output assertions
# ---------------------------------------------------------------------------
def test_tier1_thumb_dimensions_format_size(spec: RenderSpec, small_master_renderer) -> None:
    body = render_tier(spec, TIER_THUMB, master_renderer=small_master_renderer)
    img = Image.open(io.BytesIO(body))
    assert img.format == "JPEG"
    assert max(img.size) == 400
    assert len(body) <= 100 * 1024, f"thumb is {len(body)} bytes (>100KB)"


def test_tier2_preview_dimensions_format_size(spec: RenderSpec, small_master_renderer) -> None:
    body = render_tier(spec, TIER_PREVIEW, master_renderer=small_master_renderer)
    img = Image.open(io.BytesIO(body))
    assert img.format == "JPEG"
    assert max(img.size) == 2400
    assert len(body) <= 700 * 1024, f"preview is {len(body)} bytes (>700KB)"


def test_tier2_preview_has_watermark(spec: RenderSpec, small_master_renderer) -> None:
    """Render tier 2 with and without watermark; assert pixels differ.

    We test by rendering two specs and comparing pixel-level statistics:
    a tier-2 (watermark on) vs. a manually-resized master at the same dims
    (watermark off). The watermark must alter at least 5% of pixels.
    """
    body_tier2 = render_tier(spec, TIER_PREVIEW, master_renderer=small_master_renderer)
    img_tier2 = np.asarray(Image.open(io.BytesIO(body_tier2)).convert("RGB"))

    # Build an unwatermarked reference at the same dims.
    master = small_master_renderer(spec, PRINT_CANVAS_WIDTH, PRINT_CANVAS_HEIGHT)
    scale = 2400 / max(master.size)
    ref = master.resize(
        (max(1, int(round(master.size[0] * scale))), max(1, int(round(master.size[1] * scale)))),
        Image.Resampling.LANCZOS,
    )
    ref_arr = np.asarray(ref.convert("RGB"))

    # Sizes should be equal (or off-by-one). Crop both to min common size.
    h = min(img_tier2.shape[0], ref_arr.shape[0])
    w = min(img_tier2.shape[1], ref_arr.shape[1])
    delta_mask = np.any(img_tier2[:h, :w] != ref_arr[:h, :w], axis=-1)
    differ_pct = float(delta_mask.sum()) / float(delta_mask.size)
    assert differ_pct >= 0.05, f"tier-2 only differs from unwatermarked by {differ_pct:.2%}"


def test_tier3_print_dimensions_format_no_watermark(
    spec: RenderSpec, small_master_renderer
) -> None:
    body = render_tier(spec, TIER_PRINT, master_renderer=small_master_renderer)
    img = Image.open(io.BytesIO(body))
    assert img.format == "PNG"
    assert img.size == (PRINT_CANVAS_WIDTH, PRINT_CANVAS_HEIGHT)

    # No watermark = should match the master pixel-perfect (encode-decode tolerance).
    master = small_master_renderer(spec, PRINT_CANVAS_WIDTH, PRINT_CANVAS_HEIGHT)
    master_arr = np.asarray(master.convert("RGB"))
    img_arr = np.asarray(img.convert("RGB"))
    # PNG is lossless — equality must hold exactly.
    np.testing.assert_array_equal(master_arr, img_arr)
