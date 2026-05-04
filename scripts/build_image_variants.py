"""Build derived size variants alongside each master PNG.

For every ``output/master/<style>/<slug>.png`` we produce two derived files:

* ``output/master_thumbs/<style>/<slug>.jpg`` — JPEG, 256px on the long edge,
  quality 80. Target size: 10-30KB. Used by the species picker grid in
  ``/create`` and the admin dashboard table thumbs.
* ``output/master_previews/<style>/<slug>.webp`` — WebP, 1024px on the long
  edge, quality 85. Target size: 80-200KB. Used by the live editor canvas
  in ``/create``. Final paid downloads still use the original master PNG.

The script is idempotent: existing variants are skipped unless the master
is newer (mtime check) or ``--force`` is passed.

Usage::

    python -m scripts.build_image_variants                # build any missing
    python -m scripts.build_image_variants --force        # rebuild all
    python -m scripts.build_image_variants --slug bluegill # one species
    python -m scripts.build_image_variants --style scientific --slug bluegill
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PIL import Image

from config.settings import MASTER_DIR, OUTPUT_DIR

logger = logging.getLogger(__name__)

THUMB_DIR_NAME = "master_thumbs"
PREVIEW_DIR_NAME = "master_previews"

THUMB_LONG_EDGE = 256
PREVIEW_LONG_EDGE = 1024
THUMB_QUALITY = 80
PREVIEW_QUALITY = 85


def _thumb_dir() -> Path:
    return Path(OUTPUT_DIR) / THUMB_DIR_NAME


def _preview_dir() -> Path:
    return Path(OUTPUT_DIR) / PREVIEW_DIR_NAME


def thumb_path(style: str, slug: str) -> Path:
    return _thumb_dir() / style / f"{slug}.jpg"


def preview_path(style: str, slug: str) -> Path:
    return _preview_dir() / style / f"{slug}.webp"


def _resize_long_edge(img: Image.Image, target: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= target:
        return img.copy()
    if w >= h:
        new_w = target
        new_h = int(round(h * (target / w)))
    else:
        new_h = target
        new_w = int(round(w * (target / h)))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _is_stale(src: Path, dst: Path) -> bool:
    """Return True if dst is missing or older than src."""
    if not dst.exists():
        return True
    try:
        return dst.stat().st_mtime < src.stat().st_mtime
    except OSError:
        return True


def build_variants_for(
    master_png: Path,
    style: str,
    slug: str,
    *,
    force: bool = False,
) -> tuple[bool, bool]:
    """Build thumb + preview for one master. Returns (thumb_built, preview_built)."""
    t_path = thumb_path(style, slug)
    p_path = preview_path(style, slug)

    need_thumb = force or _is_stale(master_png, t_path)
    need_preview = force or _is_stale(master_png, p_path)
    if not need_thumb and not need_preview:
        return (False, False)

    with Image.open(master_png) as img:
        # JPEG/WebP need RGB; flatten any alpha against white.
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            base = bg
        elif img.mode != "RGB":
            base = img.convert("RGB")
        else:
            base = img.copy()

        thumb_built = False
        preview_built = False

        if need_thumb:
            t_path.parent.mkdir(parents=True, exist_ok=True)
            thumb_img = _resize_long_edge(base, THUMB_LONG_EDGE)
            thumb_img.save(
                t_path,
                "JPEG",
                quality=THUMB_QUALITY,
                optimize=True,
                progressive=True,
            )
            thumb_built = True

        if need_preview:
            p_path.parent.mkdir(parents=True, exist_ok=True)
            preview_img = _resize_long_edge(base, PREVIEW_LONG_EDGE)
            preview_img.save(
                p_path,
                "WEBP",
                quality=PREVIEW_QUALITY,
                method=6,
            )
            preview_built = True

    return (thumb_built, preview_built)


def iter_masters(
    style: str | None = None,
    slug: str | None = None,
) -> list[tuple[Path, str, str]]:
    """Walk MASTER_DIR and yield (path, style_slug, species_slug)."""
    root = Path(MASTER_DIR)
    if not root.exists():
        return []
    out: list[tuple[Path, str, str]] = []
    style_dirs = [root / style] if style else [d for d in root.iterdir() if d.is_dir()]
    for sdir in style_dirs:
        if not sdir.is_dir():
            continue
        st = sdir.name
        for png in sdir.glob("*.png"):
            sp = png.stem
            if slug and sp != slug:
                continue
            out.append((png, st, sp))
    return out


def build_all(
    *,
    style: str | None = None,
    slug: str | None = None,
    force: bool = False,
) -> tuple[int, int, int]:
    """Build variants for every master matching the filters.

    Returns ``(masters_seen, thumbs_built, previews_built)``.
    """
    masters = iter_masters(style=style, slug=slug)
    thumbs = 0
    previews = 0
    for path, st, sp in masters:
        try:
            built_thumb, built_preview = build_variants_for(
                path, st, sp, force=force
            )
        except Exception as exc:  # noqa: BLE001 - keep going on per-file errors
            logger.error("Failed to build variants for %s/%s: %s", st, sp, exc)
            continue
        if built_thumb:
            thumbs += 1
        if built_preview:
            previews += 1
        if built_thumb or built_preview:
            logger.info(
                "%s/%s -> %s%s",
                st,
                sp,
                "thumb " if built_thumb else "",
                "preview" if built_preview else "",
            )
    return (len(masters), thumbs, previews)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build derived JPEG thumb + WebP preview variants from masters."
    )
    parser.add_argument("--style", default=None, help="Limit to one style slug")
    parser.add_argument("--slug", default=None, help="Limit to one species slug")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if variants exist and are up-to-date",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    seen, thumbs, previews = build_all(
        style=args.style, slug=args.slug, force=args.force
    )
    logger.info(
        "build_image_variants done. masters=%d thumbs_built=%d previews_built=%d",
        seen,
        thumbs,
        previews,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
