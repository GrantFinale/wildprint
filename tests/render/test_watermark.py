"""Tests for the diagonal watermark function."""
from __future__ import annotations

import inspect

import numpy as np
from PIL import Image

from review_app.render.watermark import apply_watermark


def _solid(width: int, height: int, color: tuple[int, int, int] = (40, 40, 40)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _solid_rgba(width: int, height: int, color: tuple[int, int, int, int] = (40, 40, 40, 255)) -> Image.Image:
    return Image.new("RGBA", (width, height), color)


def test_apply_watermark_returns_same_size_image() -> None:
    src = _solid(800, 1200)
    out = apply_watermark(src)
    assert out.size == src.size


def test_apply_watermark_preserves_input_mode_rgb() -> None:
    src = _solid(400, 600)
    out = apply_watermark(src)
    assert out.mode == "RGB"


def test_apply_watermark_preserves_input_mode_rgba() -> None:
    src = _solid_rgba(400, 600)
    out = apply_watermark(src)
    assert out.mode == "RGBA"


def test_apply_watermark_changes_pixels() -> None:
    """The watermark must touch a non-trivial number of pixels.

    At the default 6% opacity the actual touched-pixel area is the text
    glyphs only (~0.1-2% of the image — many anti-aliased edge pixels
    round to no-change at low opacity). We assert >= 0.05% as a sanity
    floor — anything less means the watermark function didn't actually
    draw anything.

    For a stronger semantic check, we ALSO render the watermark at high
    opacity (50%) and assert the high-opacity render touches more pixels
    than the low-opacity one — proving opacity actually scales the effect.
    """
    src = _solid(800, 1200)
    out = apply_watermark(src)

    src_arr = np.asarray(src.convert("RGB"))
    out_arr = np.asarray(out.convert("RGB"))

    # A pixel "differs" if any channel differs.
    diff_mask = np.any(src_arr != out_arr, axis=-1)
    differ_pct = float(diff_mask.sum()) / float(diff_mask.size)
    assert differ_pct >= 0.0005, f"watermark only changed {differ_pct:.2%} of pixels"

    # Higher opacity => more pixels visibly differ.
    out_high = apply_watermark(src, opacity=0.50)
    high_arr = np.asarray(out_high.convert("RGB"))
    diff_high = float(np.any(src_arr != high_arr, axis=-1).sum()) / float(diff_mask.size)
    assert diff_high > differ_pct, (
        f"opacity scaling broken: low={differ_pct:.2%} high={diff_high:.2%}"
    )


def test_apply_watermark_handles_rgba_and_rgb() -> None:
    """Both RGB and RGBA inputs work end-to-end without exceptions."""
    rgb = _solid(300, 400)
    rgba = _solid_rgba(300, 400)
    apply_watermark(rgb)
    apply_watermark(rgba)


def test_apply_watermark_opacity_validation() -> None:
    """Opacity outside [0, 1] must raise."""
    src = _solid(100, 100)
    import pytest

    with pytest.raises(ValueError):
        apply_watermark(src, opacity=1.5)
    with pytest.raises(ValueError):
        apply_watermark(src, opacity=-0.1)


def test_apply_watermark_empty_text_rejected() -> None:
    src = _solid(100, 100)
    import pytest

    with pytest.raises(ValueError):
        apply_watermark(src, text="")


def test_apply_watermark_low_opacity_changes_fewer_pixels_than_high() -> None:
    """Sanity: lower opacity => fewer pixels change visibly (or by smaller amounts)."""
    src = _solid(800, 1200)
    low = apply_watermark(src, opacity=0.05)
    high = apply_watermark(src, opacity=0.50)

    src_arr = np.asarray(src.convert("RGB"))
    low_arr = np.asarray(low.convert("RGB"))
    high_arr = np.asarray(high.convert("RGB"))

    # Mean absolute pixel delta — high opacity should produce a larger delta.
    low_delta = float(np.mean(np.abs(low_arr.astype(int) - src_arr.astype(int))))
    high_delta = float(np.mean(np.abs(high_arr.astype(int) - src_arr.astype(int))))
    assert high_delta > low_delta


def test_watermark_uses_www_prefix_by_default() -> None:
    """Default text must be the www-prefixed brand string."""
    sig = inspect.signature(apply_watermark)
    default_text = sig.parameters["text"].default
    assert default_text == "www.fishingposter.com"


def test_watermark_default_opacity_is_0_06() -> None:
    """Default opacity must be 0.06 (6%) — more transparent than the old 10%."""
    sig = inspect.signature(apply_watermark)
    default_opacity = sig.parameters["opacity"].default
    assert default_opacity == 0.06
