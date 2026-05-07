"""Tier configuration constants — the single source of truth for tier specs.

Specs are locked by ``docs/integration-plan.md`` decision #5 and reproduced
in ``docs/render-tiers.md``. Bucket names resolve from environment so dev
and prod can override.

Tier 1 — thumbnail
    400 px JPEG q85, no watermark, public-read on ``fishingposter-thumbs``.

Tier 2 — preview
    2400 px JPEG q85 (with optional 1800 px srcset variant), watermarked,
    public-read on ``fishingposter-previews``.

Tier 3 — print
    7200 x 10800 PNG sRGB, no watermark, private on ``fishingposter-posters``.
    Signed URLs only. Generated post-payment via the RQ worker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Tier ordinals
# ---------------------------------------------------------------------------
TIER_THUMB: Final[int] = 1
TIER_PREVIEW: Final[int] = 2
TIER_PRINT: Final[int] = 3

# A 1800 px srcset variant is also published for tier 2 (non-retina fallback).
# We list it here for documentation but the rendered output is tier-2 itself
# scaled down on demand by the storage URL helper. Phase 2 doesn't ship the
# srcset variant — see ``docs/render-tiers.md`` for the deferred plan.
PREVIEW_SRCSET_LONG_EDGE: Final[int] = 1800

# Print master dimensions — fixed at 7200 x 10800 (24" x 36" @ 300 DPI).
PRINT_CANVAS_WIDTH: Final[int] = 7200
PRINT_CANVAS_HEIGHT: Final[int] = 10800


# ---------------------------------------------------------------------------
# TierConfig dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TierConfig:
    """Frozen, hashable tier configuration. One instance per tier."""

    tier: int
    long_edge_px: int
    dpi: int
    fmt: Literal["JPEG", "PNG"]
    jpeg_quality: int  # ignored for PNG
    watermark: bool
    public: bool  # True => ACL=public-read; False => private + signed URL only
    bucket_env: str  # env var holding the bucket name
    default_bucket: str  # fallback if env var unset (dev convenience)
    content_type: str

    def bucket(self) -> str:
        """Resolve the bucket name from env, falling back to the default."""
        return os.environ.get(self.bucket_env, self.default_bucket)


# ---------------------------------------------------------------------------
# TIER_CONFIG — the locked spec table
# ---------------------------------------------------------------------------
TIER_CONFIG: Final[dict[int, TierConfig]] = {
    TIER_THUMB: TierConfig(
        tier=TIER_THUMB,
        long_edge_px=400,
        dpi=72,
        fmt="JPEG",
        jpeg_quality=85,
        watermark=False,
        public=True,
        bucket_env="SPACES_THUMBS_BUCKET",
        default_bucket="fishingposter-thumbs",
        content_type="image/jpeg",
    ),
    TIER_PREVIEW: TierConfig(
        tier=TIER_PREVIEW,
        long_edge_px=2400,
        dpi=72,
        fmt="JPEG",
        jpeg_quality=85,
        watermark=True,
        public=True,
        bucket_env="SPACES_PREVIEWS_BUCKET",
        default_bucket="fishingposter-previews",
        content_type="image/jpeg",
    ),
    TIER_PRINT: TierConfig(
        tier=TIER_PRINT,
        long_edge_px=PRINT_CANVAS_HEIGHT,  # the long edge of 7200x10800
        dpi=300,
        fmt="PNG",
        jpeg_quality=0,  # n/a
        watermark=False,
        public=False,
        bucket_env="SPACES_POSTERS_BUCKET",
        default_bucket="fishingposter-posters",
        content_type="image/png",
    ),
}


def get_tier_config(tier: int) -> TierConfig:
    """Return the :class:`TierConfig` for ``tier`` or raise ``ValueError``."""
    cfg = TIER_CONFIG.get(tier)
    if cfg is None:
        raise ValueError(
            f"Unknown tier {tier!r}; valid: {sorted(TIER_CONFIG.keys())!r}"
        )
    return cfg


__all__ = [
    "PREVIEW_SRCSET_LONG_EDGE",
    "PRINT_CANVAS_HEIGHT",
    "PRINT_CANVAS_WIDTH",
    "TIER_CONFIG",
    "TIER_PREVIEW",
    "TIER_PRINT",
    "TIER_THUMB",
    "TierConfig",
    "get_tier_config",
]
