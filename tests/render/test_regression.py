"""Regression test: three-tier composition match.

Asserts that tier-1, tier-2, and tier-3 renders for the same spec produce
visually equivalent compositions. Catches drift if a future renderer change
inadvertently makes tier-2 (preview) show different content than tier-3
(print) — the kind of bug that ships posters that don't match what the
customer saw.

Strategy: render all three tiers from a deterministic procedural master,
downscale tier-3 (print) to tier-2's pixel count, compare via SSIM on the
luminance channel. SSIM > 0.95 = "structurally identical".

Watermark caveat: tier-2 has a watermark; tier-3 does not. We compare AGAINST
a separately-rendered "tier-2 without watermark" reference so the structural
similarity test isn't fooled by watermark deltas. The test fixture renders
the master at low resolution so the test runs in <5s.

This test depends on ``scikit-image`` for SSIM. If that's not installed it
falls back to a mean-pixel-diff threshold which is less robust but lets
the suite run on minimal CI images.
"""
from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from PIL import Image

from review_app.render.renderer import render_tier
from review_app.render.spec import RenderSpec
from review_app.render.tiers import TIER_PREVIEW, TIER_PRINT, TIER_THUMB


# ---------------------------------------------------------------------------
# Procedural deterministic test master
# ---------------------------------------------------------------------------
# Use a SMALLER print canvas than production to keep this test fast. We'll
# monkey-patch the constants via the master_renderer's intermediate dims,
# which means we render directly through render_tier with a custom factory
# that ignores the requested dims and produces the smaller canvas.
#
# This sidesteps PRINT_CANVAS_* constants — the test is about composition
# equivalence ACROSS tiers, not about hitting prod dimensions.

TEST_W = 720
TEST_H = 1080


def _seeded_master(spec: RenderSpec, w: int, h: int) -> Image.Image:
    """Return a deterministic test master ignoring the requested dims.

    Uses (TEST_W, TEST_H) for speed. Same RenderSpec produces identical
    pixels every call (numpy seeded by spec hash).
    """
    seed = int(spec.canonical_hash()[:8], 16)
    rng = np.random.default_rng(seed)
    arr = np.zeros((TEST_H, TEST_W, 3), dtype=np.uint8)

    # Background gradient
    x = np.linspace(40, 200, TEST_W, dtype=np.uint8)
    y = np.linspace(40, 200, TEST_H, dtype=np.uint8)
    arr[..., 0] = (x[None, :].astype(int) + y[:, None].astype(int)) // 2
    arr[..., 1] = arr[..., 0] // 2 + 60
    arr[..., 2] = arr[..., 0] // 3 + 80

    # Five color "subjects" placed deterministically based on the seed
    n_subjects = 5
    subject_w = TEST_W // 6
    subject_h = TEST_H // 8
    for i in range(n_subjects):
        cx = int(TEST_W * (0.15 + 0.15 * i))
        cy = int(TEST_H * (0.30 + 0.10 * i))
        color = tuple(int(c) for c in rng.integers(80, 220, size=3))
        x0 = max(0, cx - subject_w // 2)
        y0 = max(0, cy - subject_h // 2)
        x1 = min(TEST_W, cx + subject_w // 2)
        y1 = min(TEST_H, cy + subject_h // 2)
        arr[y0:y1, x0:x1] = color

    # Bold horizontal stripe near the top to make orientation obvious
    arr[20:60, :, :] = (240, 240, 240)
    return Image.fromarray(arr, mode="RGB")


# ---------------------------------------------------------------------------
# Patch tier 3's "PRINT_CANVAS_*" constants for the test so we don't render
# 7200x10800. We achieve this by patching render_tier indirectly: the test
# renderer ignores dim hints and the resulting tier-3 image dims fall out
# of whatever the master returns. We wrap render_tier to skip the print=
# master verbatim path's strict assertion.
# ---------------------------------------------------------------------------


def _ssim_or_mse(a: np.ndarray, b: np.ndarray) -> tuple[float, str]:
    """Return (similarity, metric_name) — SSIM if available, else 1 - normalized MSE."""
    try:
        from skimage.metrics import structural_similarity as ssim  # type: ignore[import-untyped]

        # Convert to luminance for a perceptual comparison
        a_lum = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.uint8)
        b_lum = (0.299 * b[..., 0] + 0.587 * b[..., 1] + 0.114 * b[..., 2]).astype(np.uint8)
        score = float(ssim(a_lum, b_lum, data_range=255))
        return score, "ssim"
    except Exception:
        # Fallback: mean absolute pixel diff -> similarity in [0, 1]
        diff = np.mean(np.abs(a.astype(int) - b.astype(int))) / 255.0
        return float(1.0 - diff), "1-mae"


@pytest.fixture
def spec() -> RenderSpec:
    return RenderSpec(
        lake="Regression Lake",
        species=["bass", "trout", "perch"],
        art_style="editorial-v1",
        layout_config={"grid": 3, "padding": 48},
    )


def _patched_print_canvas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the print-canvas constants in the renderer module so tier-3
    fits the small test master."""
    import review_app.render.renderer as r_mod

    monkeypatch.setattr(r_mod, "PRINT_CANVAS_WIDTH", TEST_W)
    monkeypatch.setattr(r_mod, "PRINT_CANVAS_HEIGHT", TEST_H)


def test_three_tiers_match_composition(
    spec: RenderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 1, 2, 3 of the same spec must depict the same composition.

    Tier-3 (print, no watermark) is the ground truth. We downscale it to
    tier-2 dims (modulo aspect ratio) and tier-1 dims, then compare each
    against the renderer's output for that tier.

    Because tier-2 has a watermark, we compare it against a watermark-free
    reference (a fresh re-render with watermarking patched off) — this
    isolates the structural similarity test from the watermark deltas.
    """
    _patched_print_canvas(monkeypatch)

    # Render tier 3 (ground truth, no watermark)
    body3 = render_tier(spec, TIER_PRINT, master_renderer=_seeded_master)
    img3 = Image.open(io.BytesIO(body3)).convert("RGB")
    assert img3.size == (TEST_W, TEST_H)
    arr3 = np.asarray(img3)

    # Render tier 2 (watermarked) — compare against a watermark-free version.
    # Patch apply_watermark to the identity so we get the underlying composition.
    import review_app.render.renderer as r_mod

    monkeypatch.setattr(
        r_mod, "apply_watermark", lambda img, **kw: img
    )
    body2 = render_tier(spec, TIER_PREVIEW, master_renderer=_seeded_master)
    img2 = Image.open(io.BytesIO(body2)).convert("RGB")

    # Downscale tier-3 to tier-2's exact pixel count for a fair comparison.
    img3_down_to_2 = img3.resize(img2.size, Image.Resampling.LANCZOS)
    arr3_d2 = np.asarray(img3_down_to_2)
    arr2 = np.asarray(img2)

    sim_2v3, metric = _ssim_or_mse(arr2, arr3_d2)
    assert sim_2v3 >= 0.95, (
        f"tier-2 vs tier-3 {metric}={sim_2v3:.3f} (< 0.95) — composition drift!"
    )

    # Render tier 1 — compare to a downsampled tier-3.
    body1 = render_tier(spec, TIER_THUMB, master_renderer=_seeded_master)
    img1 = Image.open(io.BytesIO(body1)).convert("RGB")
    img3_down_to_1 = img3.resize(img1.size, Image.Resampling.LANCZOS)
    arr1 = np.asarray(img1)
    arr3_d1 = np.asarray(img3_down_to_1)

    sim_1v3, metric1 = _ssim_or_mse(arr1, arr3_d1)
    # JPEG at 400 px is more lossy; relax threshold slightly.
    assert sim_1v3 >= 0.92, (
        f"tier-1 vs tier-3 {metric1}={sim_1v3:.3f} (< 0.92) — composition drift!"
    )
