"""Bucket-name resolution + tier→bucket mapping.

Bucket names come from environment variables so dev/staging/prod can point
at different physical buckets without code changes. Each function is
evaluated lazily so importing this module doesn't require env to be set.

Tier mapping (from `docs/integration-plan.md` §"Three-tier render"):
- Tier 1 (thumbnail, 400 px) → `THUMBS_BUCKET` (public-read, CDN)
- Tier 2 (preview, 2400 px, watermarked) → `PREVIEWS_BUCKET` (public-read, CDN)
- Tier 3 (print, 5400x7200, no watermark) → `POSTERS_BUCKET` (private, signed URLs)
"""
from __future__ import annotations

import os


def _resolve(env_var: str, default: str) -> str:
    """Return env value or the documented default.

    The defaults match the live nyc3 buckets so production doesn't need
    additional env vars; dev environments override via `.env` to point at
    sandbox buckets.
    """
    return os.environ.get(env_var) or default


def posters_bucket() -> str:
    """Tier 3 — private bucket holding 5400x7200 PNG print masters."""
    return _resolve("SPACES_POSTERS_BUCKET", "fishingposter-posters")


def previews_bucket() -> str:
    """Tier 2 — public-read bucket holding watermarked 2400 px JPEG previews."""
    return _resolve("SPACES_PREVIEWS_BUCKET", "fishingposter-previews")


def thumbs_bucket() -> str:
    """Tier 1 — public-read bucket holding 400 px JPEG thumbnails."""
    return _resolve("SPACES_THUMBS_BUCKET", "fishingposter-thumbs")


def bucket_for_tier(tier: int) -> str:
    """Return the bucket name for a render tier.

    Args:
        tier: 1 (thumb), 2 (preview), or 3 (print).
    """
    if tier == 1:
        return thumbs_bucket()
    if tier == 2:
        return previews_bucket()
    if tier == 3:
        return posters_bucket()
    raise ValueError(f"tier must be 1, 2, or 3 (got {tier!r})")


# ---------------------------------------------------------------------------
# Convenience module-level constants
# ---------------------------------------------------------------------------
# These are computed at import time; they reflect whatever the env was at
# import. Most code should call the functions above for late binding, but
# legacy / one-off scripts may prefer constants.
POSTERS_BUCKET: str = posters_bucket()
PREVIEWS_BUCKET: str = previews_bucket()
THUMBS_BUCKET: str = thumbs_bucket()


__all__ = [
    "POSTERS_BUCKET",
    "PREVIEWS_BUCKET",
    "THUMBS_BUCKET",
    "bucket_for_tier",
    "posters_bucket",
    "previews_bucket",
    "thumbs_bucket",
]
