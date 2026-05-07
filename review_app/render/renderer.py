"""Tier render wrapper.

Owns the *only* call site that knows how to:

1. Produce the master image for a :class:`RenderSpec` (delegates to a
   ``MasterRenderer`` callable — defaults to the ``poster_layout`` package's
   :class:`EditorialPosterRenderer`, but tests inject a fake).
2. Resize / compress / watermark per :class:`TierConfig`.
3. Return the resulting bytes.

The existing ``poster_layout/renderer.py`` is the crown jewel — we wrap it,
we never modify it.

Memory contract
---------------
Tier 3 must peak at < 2 GB working memory on the worker. The bottleneck is
the master itself: 7200 x 10800 RGB = ~233 MB raw, and PIL keeps a few
intermediate copies during save. We:

* Avoid materializing PNG bytes twice (write straight into a
  :class:`io.BytesIO` and ``getvalue()`` once).
* Don't keep the master alive after we have the tier output.
* For tier 1/2, ``Image.thumbnail`` resizes in-place which avoids one full
  copy vs. ``Image.resize``.
"""
from __future__ import annotations

import io
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from PIL import Image

from review_app.render.spec import RenderSpec
from review_app.render.tiers import (
    PRINT_CANVAS_HEIGHT,
    PRINT_CANVAS_WIDTH,
    TIER_PREVIEW,
    TIER_PRINT,
    TIER_THUMB,
    TierConfig,
    get_tier_config,
)
from review_app.render.watermark import apply_watermark

if TYPE_CHECKING:
    pass


_log = logging.getLogger(__name__)
_struct_log = structlog.get_logger("render.tiers")


# A MasterRenderer takes a spec + target dimensions and returns the master
# image (an RGB or RGBA PIL Image at the requested canvas size). The default
# implementation calls into ``poster_layout``; tests inject a fake that
# returns a procedurally-generated test card.
MasterRenderer = Callable[[RenderSpec, int, int], Image.Image]


def _default_master_renderer(
    spec: RenderSpec, width: int, height: int
) -> Image.Image:
    """Default master-image producer.

    Wraps ``poster_layout.EditorialPosterRenderer`` for now. Imported lazily
    so unit tests that mock the renderer don't pull in the full poster_layout
    dependency tree.

    Phase 2 ships the wrapper; the actual route-handler cutover (which will
    pass real :class:`LayoutResult` objects through here) lands in Phase 3.
    Until then this raises so an accidental call in a code path we forgot
    to wire up fails loudly rather than silently producing garbage.
    """
    raise NotImplementedError(
        "_default_master_renderer is a Phase 3 wiring placeholder. "
        "Pass a custom `master_renderer` to render_tier() until the "
        "route handlers are migrated. See docs/render-tiers.md."
    )


def _resize_to_long_edge(
    image: Image.Image, long_edge: int
) -> Image.Image:
    """Return a new image with the long edge equal to ``long_edge`` px.

    Preserves aspect ratio. Uses LANCZOS for high-quality downscale.
    """
    w, h = image.size
    if max(w, h) == long_edge:
        return image
    scale = long_edge / max(w, h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    return image.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)


def _encode_jpeg(image: Image.Image, quality: int, dpi: int) -> bytes:
    """Encode ``image`` as JPEG. RGBA inputs are flattened on white."""
    if image.mode == "RGBA":
        # JPEG can't store alpha. Composite onto white before save.
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")

    buf = io.BytesIO()
    image.save(
        buf,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        dpi=(dpi, dpi),
    )
    return buf.getvalue()


def _encode_png(image: Image.Image, dpi: int) -> bytes:
    """Encode ``image`` as PNG. sRGB profile is implied (PIL default)."""
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False, dpi=(dpi, dpi))
    return buf.getvalue()


def render_tier(
    spec: RenderSpec,
    tier: int,
    *,
    master_renderer: MasterRenderer | None = None,
) -> bytes:
    """Render ``spec`` at ``tier`` and return the encoded image bytes.

    Args:
        spec: the canonical render spec.
        tier: 1 (thumb), 2 (preview), or 3 (print).
        master_renderer: optional dependency injection. If None, falls back
            to :func:`_default_master_renderer`. Tests pass a fake.

    Returns:
        Encoded image bytes (JPEG for tier 1/2, PNG for tier 3).

    Logs (structured): tier, spec_hash (first 12 chars), render_ms,
    master_ms, encode_ms, output_size_bytes, dimensions.
    """
    cfg: TierConfig = get_tier_config(tier)
    spec_hash = spec.canonical_hash()
    short_hash = spec_hash[:12]

    t_total = time.perf_counter()

    # --- Step 1: produce the master image at print canvas dimensions ---
    t_master = time.perf_counter()
    renderer_fn = master_renderer or _default_master_renderer
    master = renderer_fn(spec, PRINT_CANVAS_WIDTH, PRINT_CANVAS_HEIGHT)
    master_ms = int((time.perf_counter() - t_master) * 1000)

    # --- Step 2: resize per tier ---
    t_resize = time.perf_counter()
    if tier == TIER_PRINT:
        # Print = master verbatim. No resize, no resample artifacts.
        sized = master
    else:
        sized = _resize_to_long_edge(master, cfg.long_edge_px)
        # Free the master ASAP — tier 1/2 don't need it past this point.
        master.close()
    resize_ms = int((time.perf_counter() - t_resize) * 1000)

    # --- Step 3: watermark (tier 2 only) ---
    t_watermark = time.perf_counter()
    if cfg.watermark:
        watermarked = apply_watermark(sized)
        # Only close the previous canvas if the watermarker returned a NEW
        # image (the normal path). A test-injected identity-watermarker
        # returns the same object — closing it would invalidate `sized`.
        if watermarked is not sized:
            sized.close()
        sized = watermarked
    watermark_ms = int((time.perf_counter() - t_watermark) * 1000)

    # --- Step 4: encode ---
    t_encode = time.perf_counter()
    if cfg.fmt == "JPEG":
        out = _encode_jpeg(sized, cfg.jpeg_quality, cfg.dpi)
    else:
        out = _encode_png(sized, cfg.dpi)
    encode_ms = int((time.perf_counter() - t_encode) * 1000)

    # Cleanup
    width, height = sized.size
    sized.close()

    total_ms = int((time.perf_counter() - t_total) * 1000)
    output_size = len(out)

    _struct_log.info(
        "render_tier complete",
        tier=tier,
        spec_hash=short_hash,
        renderer_version=spec.renderer_version,
        render_ms=total_ms,
        master_ms=master_ms,
        resize_ms=resize_ms,
        watermark_ms=watermark_ms,
        encode_ms=encode_ms,
        output_size_bytes=output_size,
        width=width,
        height=height,
        fmt=cfg.fmt,
    )

    return out


__all__ = [
    "TIER_PREVIEW",
    "TIER_PRINT",
    "TIER_THUMB",
    "MasterRenderer",
    "render_tier",
]
