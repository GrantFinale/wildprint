"""Normalize raw generated images into print-ready PNGs.

Takes images from ``output/raw/{style}/{species}/*.png``, crops them to
content bounding box, composites onto a pure white canvas of
``(CANVAS_WIDTH, CANVAS_HEIGHT)``, and writes the result to
``output/normalized/{style}/{species}/*.png``. The corresponding manifest
entry's ``normalized_path`` field is updated.

Design notes
------------
- We intentionally do not denoise, sharpen, or quantize colors. The goal is
  to preserve the artistic character of the source image. All we do is
  crop/resize/compose.
- Background detection uses a simple threshold (``WHITE_BG_THRESHOLD``):
  pixels with R, G, and B all strictly greater than the threshold are
  treated as background. Providers are instructed to produce images on
  white, so this is robust in practice.
- ``--auto-flip`` is a best-effort heuristic for left-facing orientation.
  It compares average darkness of the left third vs the right third of the
  content and flips if the right side is darker (assuming the head/face is
  the busier region). Users should review outputs and override with
  ``--no-flip`` if the heuristic gets it wrong.

Run as::

    python -m scripts.normalize_images [--species SLUG] [--style SLUG] \
        [--auto-flip | --no-flip] [--force] [--dry-run]
"""
from __future__ import annotations


from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from config.settings import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    NORMALIZED_DIR,
)
from scripts.build_manifest import load_manifest, save_manifest

logger = logging.getLogger(__name__)

# Fraction of the canvas the content should occupy along its limiting axis.
# Kept high (0.97) so normalized masters are tight around the subject —
# downstream renderers want the fish to dominate, not swim in padding.
_CONTENT_FIT: float = 0.97
# Padding around the cropped content bbox, as a fraction of the longer side.
_PADDING_FRAC: float = 0.015
# Standard white paper tone used for the normalized canvas background.
# Matches the base prompt's "pure white background (#FFFFFF)" instruction.
# Content detection is corner-sample-based so this threshold is independent
# of the normalized canvas color — it infers the source's actual background.
_PAPER_BG_RGB: tuple[int, int, int] = (255, 255, 255)
# Per-channel tolerance for corner-sampled background detection. Pixels
# whose difference from the sampled background color exceeds this value on
# any channel are treated as content.
_BG_TOLERANCE: int = 14


def detect_background_color(img: Image.Image) -> tuple[int, int, int]:
    """Sample the four corners of ``img`` and return a median RGB background.

    Works for any paper tone (pure white, warm ivory, cool cream, etc.) —
    we simply trust that the corners are background. If the image has
    transparent corners, those samples are skipped.
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    samples: list[tuple[int, int, int]] = []
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        px = rgba.getpixel((x, y))
        if len(px) == 4 and px[3] < 255:
            continue
        samples.append((px[0], px[1], px[2]))
    if not samples:
        # All corners transparent. Fall back to ivory.
        return _PAPER_BG_RGB
    # Median per channel — robust to one weird corner.
    r = sorted(s[0] for s in samples)[len(samples) // 2]
    g = sorted(s[1] for s in samples)[len(samples) // 2]
    b = sorted(s[2] for s in samples)[len(samples) // 2]
    return (r, g, b)


def find_content_bbox(
    img: Image.Image, tolerance: int = _BG_TOLERANCE
) -> tuple[int, int, int, int] | None:
    """Return the bounding box of non-background content in ``img``.

    Unlike the old whitepoint-threshold approach, this samples the actual
    background color from the four corners of the image, then treats any
    pixel whose per-channel difference from that background exceeds
    ``tolerance`` as content. This handles warm ivory paper (#F5EFE0) and
    other non-pure-white backgrounds without mis-cropping.

    Transparent pixels are also treated as background. Returns ``None`` if
    the image is entirely background.
    """
    from PIL import ImageChops

    rgba = img.convert("RGBA")
    bg = detect_background_color(rgba)

    # Compare only the RGB channels against a uniform background plate.
    rgb = rgba.convert("RGB")
    plate = Image.new("RGB", rgb.size, bg)
    diff = ImageChops.difference(rgb, plate)  # per-channel absolute diff

    # Collapse to a single grayscale channel by taking the max per pixel.
    r_diff, g_diff, b_diff = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(r_diff, g_diff), b_diff)

    # Binary content mask using the tolerance.
    content_mask = max_diff.point(lambda v: 255 if v > tolerance else 0)

    # Respect alpha: fully transparent pixels are never content.
    alpha = rgba.split()[3]
    fg_alpha = alpha.point(lambda v: 255 if v >= 255 else 0)
    content_mask = ImageChops.multiply(content_mask, fg_alpha)

    return content_mask.getbbox()


def _alpha_key_and_unmatte(
    img: Image.Image,
    bg_rgb: tuple[int, int, int],
    low_threshold: int = 10,
    high_threshold: int = 30,
    prefilter_radius: float = 1.5,
) -> Image.Image:
    """Alpha-key the background and unmatte edge pixels.

    Built for editorial compositing onto arbitrary poster backgrounds
    (white, navy, sage, ...). The matte must be:

    1. Clean in the paper region (zero alpha, no sub-threshold leakage)
    2. Soft only at real object edges (3-5 px wide transitions)
    3. Color-accurate at the edges (unmatted, no background bleed)

    Algorithm:

    1. Pre-blur the source RGB slightly (radius ``prefilter_radius``) to
       smooth out paper-noise variations. This lets us use a tight
       threshold floor without introducing pepper artifacts. The blur is
       only used for alpha computation — unmatting reads the unblurred
       colors.
    2. Build a per-pixel "diff from background" value (max across
       channels).
    3. Apply a two-level threshold: ``diff < low_threshold`` → alpha=0,
       ``diff > high_threshold`` → alpha=255, linear ramp in between.
       A narrow band (high - low ≈ 20) gives crisp fish edges.
    4. Unmatte: solve for the true foreground color assuming the observed
       pixel is a straight-alpha blend of the true foreground over bg.

    Requires ``numpy`` for vector math. Falls back to a crude hard-key on
    systems without numpy.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning(
            "numpy not available; skipping alpha unmatting. Edges may "
            "carry a background halo when composited on a different color."
        )
        from PIL import ImageChops

        rgb = img.convert("RGB")
        plate = Image.new("RGB", rgb.size, bg_rgb)
        diff = ImageChops.difference(rgb, plate)
        r, g, b = diff.split()
        max_diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
        alpha = max_diff.point(
            lambda v, lo=low_threshold, hi=high_threshold: (
                0 if v < lo
                else 255 if v > hi
                else int((v - lo) / max(1, hi - lo) * 255)
            )
        )
        return Image.merge("RGBA", (*rgb.split(), alpha))

    from PIL import ImageFilter

    # Pre-blur for alpha computation only (smooths paper noise).
    blurred = img.convert("RGB").filter(
        ImageFilter.GaussianBlur(radius=prefilter_radius)
    )
    arr_blur = np.asarray(blurred, dtype=np.float32)
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    bg = np.asarray(bg_rgb, dtype=np.float32)

    # Per-channel absolute diff on the blurred image, max across channels.
    diff = np.abs(arr_blur - bg[None, None, :])
    max_diff = np.max(diff, axis=-1)  # (H, W)

    # Two-level threshold ramp: tight band between low and high.
    lo = float(low_threshold)
    hi = float(high_threshold)
    alpha = np.clip((max_diff - lo) / max(1.0, hi - lo) * 255.0, 0.0, 255.0)

    # Unmatte on the UNBLURRED original so foreground colors stay crisp.
    a_norm = np.clip(alpha / 255.0, 0.001, 1.0)[..., None]
    unmatted = (arr - (1.0 - a_norm) * bg[None, None, :]) / a_norm
    unmatted = np.clip(unmatted, 0.0, 255.0)

    rgba = np.concatenate([unmatted, alpha[..., None]], axis=-1).astype(
        np.uint8
    )
    return Image.fromarray(rgba, mode="RGBA")


def _should_flip(content: Image.Image) -> bool:
    """Heuristic: return True if content appears to face right and should be flipped.

    Compares mean pixel darkness of the left third vs right third of the
    content region. Darker side is presumed to contain more detail (the
    head/face). If the right third is darker, we flip.
    """
    gray = content.convert("L")
    w, h = gray.size
    if w < 3:
        return False
    third = w // 3
    left = gray.crop((0, 0, third, h))
    right = gray.crop((w - third, 0, w, h))
    # Lower mean => darker. If right is darker than left, flip.
    left_mean = sum(left.getdata()) / max(1, left.width * left.height)
    right_mean = sum(right.getdata()) / max(1, right.width * right.height)
    return right_mean < left_mean


def _pad_bbox(
    bbox: tuple[int, int, int, int], img_size: tuple[int, int], frac: float
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    w, h = img_size
    longer = max(x1 - x0, y1 - y0)
    pad = int(round(longer * frac))
    return (
        max(0, x0 - pad),
        max(0, y0 - pad),
        min(w, x1 + pad),
        min(h, y1 + pad),
    )


def normalize_image(
    raw_path: Path,
    out_path: Path,
    *,
    auto_flip: bool = False,
    dry_run: bool = False,
) -> bool:
    """Normalize a single image to a transparent-background RGBA PNG.

    Pipeline:

    1. Open the raw image and sample the four corners to detect the
       source's actual background color (handles pure white, warm ivory,
       cool cream — whatever the generation produced).
    2. Alpha-key the background using that color + an unmatting step so
       transition pixels shed any baked-in background bleed.
    3. Find the content bounding box from the alpha channel and crop.
    4. Resize to fit ``_CONTENT_FIT`` of the canvas, preserving aspect.
    5. Place on a fully transparent ``(CANVAS_WIDTH, CANVAS_HEIGHT)``
       RGBA canvas and save as PNG.

    The output is a transparent-background master that composites
    cleanly onto any downstream backdrop (white, ivory, navy, sage, ...).
    """
    try:
        with Image.open(raw_path) as src:
            rgba_src = src.convert("RGBA")
    except (OSError, Image.UnidentifiedImageError) as exc:
        logger.error("Could not open %s: %s", raw_path, exc)
        return False

    # Sample the source's background tone from its corners, then alpha-key
    # + unmatte so the output is background-independent.
    bg_rgb = detect_background_color(rgba_src)

    # Background quality check: warn if the detected corner color is dark.
    # Dark corners mean the model generated a colored/gradient background
    # instead of white/ivory — the alpha matte can't fix that cleanly.
    bg_lum = 0.2126 * bg_rgb[0] + 0.7152 * bg_rgb[1] + 0.0722 * bg_rgb[2]
    if bg_lum < 200:
        logger.warning(
            "DARK BACKGROUND detected in %s — corner color #%02X%02X%02X "
            "(luminance %.0f). The alpha matte may not strip this cleanly. "
            "Consider picking a different variation for this species.",
            raw_path.name, *bg_rgb, bg_lum,
        )

    matted = _alpha_key_and_unmatte(rgba_src, bg_rgb)

    # Find the content bbox off the alpha channel directly.
    alpha = matted.split()[3]
    bbox = alpha.getbbox()
    if bbox is None:
        logger.warning(
            "No content found in %s (fully transparent after matte); skipping",
            raw_path,
        )
        return False

    padded = _pad_bbox(bbox, matted.size, _PADDING_FRAC)
    content = matted.crop(padded)

    if auto_flip and _should_flip(content):
        logger.info("Auto-flipping %s (right side darker than left)", raw_path.name)
        content = content.transpose(Image.FLIP_LEFT_RIGHT)

    # Fit into _CONTENT_FIT of the canvas, preserving aspect ratio.
    target_w = int(CANVAS_WIDTH * _CONTENT_FIT)
    target_h = int(CANVAS_HEIGHT * _CONTENT_FIT)
    cw, ch = content.size
    scale = min(target_w / cw, target_h / ch)
    new_w = max(1, int(round(cw * scale)))
    new_h = max(1, int(round(ch * scale)))
    resized = content.resize((new_w, new_h), Image.LANCZOS)

    # Transparent RGBA canvas — masters are background-independent.
    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    offset = ((CANVAS_WIDTH - new_w) // 2, (CANVAS_HEIGHT - new_h) // 2)
    canvas.alpha_composite(resized, dest=offset)

    if dry_run:
        logger.info("[dry-run] would write %s", out_path)
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)
    logger.info("Wrote %s (alpha-keyed RGBA, bg was #%02X%02X%02X)",
                out_path, *bg_rgb)
    return True


def _normalized_path_for(record: dict[str, Any]) -> Path:
    species = record["species_slug"]
    style = record["style_slug"]
    variation = int(record["variation"])
    filename = f"{species}_{style}_v{variation}.png"
    return NORMALIZED_DIR / style / species / filename


def run(
    *,
    species: str | None = None,
    style: str | None = None,
    auto_flip: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Normalize all manifest records matching filters.

    Returns ``(processed, skipped, failed)``.
    """
    records = load_manifest()
    if not records:
        logger.warning("Manifest is empty; run scripts.build_manifest first")
        return (0, 0, 0)

    processed = skipped = failed = 0
    dirty = False

    for rec in records:
        if species and rec.get("species_slug") != species:
            continue
        if style and rec.get("style_slug") != style:
            continue

        raw_path_str = rec.get("raw_path")
        if not raw_path_str:
            logger.warning(
                "Record %s/%s v%s missing raw_path; skipping",
                rec.get("style_slug"),
                rec.get("species_slug"),
                rec.get("variation"),
            )
            skipped += 1
            continue

        raw_path = Path(raw_path_str)
        if not raw_path.is_absolute():
            # Sidecars often use project-relative paths.
            from config.settings import PROJECT_ROOT

            raw_path = (PROJECT_ROOT / raw_path).resolve()

        if not raw_path.exists():
            logger.error("Raw file missing: %s", raw_path)
            failed += 1
            continue

        out_path = _normalized_path_for(rec)

        if out_path.exists() and not force:
            logger.info("Already normalized (use --force to redo): %s", out_path)
            # Still record the path in the manifest if missing.
            if rec.get("normalized_path") != str(out_path):
                rec["normalized_path"] = str(out_path)
                dirty = True
            skipped += 1
            continue

        ok = normalize_image(
            raw_path, out_path, auto_flip=auto_flip, dry_run=dry_run
        )
        if ok:
            processed += 1
            if not dry_run:
                rec["normalized_path"] = str(out_path)
                dirty = True
        else:
            failed += 1

    if dirty and not dry_run:
        save_manifest(records)
        logger.info("Manifest updated with normalized paths")

    logger.info(
        "Done. processed=%d skipped=%d failed=%d", processed, skipped, failed
    )
    return (processed, skipped, failed)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize raw wildprint images to a pure white print canvas."
    )
    parser.add_argument("--species", type=str, default=None, help="Species slug filter")
    parser.add_argument("--style", type=str, default=None, help="Style slug filter")
    flip_group = parser.add_mutually_exclusive_group()
    flip_group.add_argument(
        "--auto-flip",
        dest="auto_flip",
        action="store_true",
        help="Flip images so the darker/detail-dense side faces left (best effort).",
    )
    flip_group.add_argument(
        "--no-flip",
        dest="auto_flip",
        action="store_false",
        help="Never flip images (default).",
    )
    parser.set_defaults(auto_flip=False)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-normalize even if the output file already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without touching disk.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run(
        species=args.species,
        style=args.style,
        auto_flip=args.auto_flip,
        force=args.force,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
