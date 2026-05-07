"""Tier configuration constants — single source of truth for tier specs.

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

Phase 6 polish: ``get_tier_config`` now consults the ``render_presets``
table first (with a 5-minute in-memory cache) and falls back to the
hardcoded :data:`TIER_CONFIG` baseline when the DB row is missing or the
DB is unavailable. Admins edit the live config from
``/admin/catalog/render-presets``; the cache is busted on save.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import Final, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier ordinals
# ---------------------------------------------------------------------------
TIER_THUMB: Final[int] = 1
TIER_PREVIEW: Final[int] = 2
TIER_PRINT: Final[int] = 3

# A 1800 px srcset variant is also published for tier 2 (non-retina fallback).
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
# TIER_CONFIG — the locked spec table (Phase 2 baseline / fallback)
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


_DEFAULT_BUCKETS: Final[dict[str, str]] = {
    "SPACES_THUMBS_BUCKET": "fishingposter-thumbs",
    "SPACES_PREVIEWS_BUCKET": "fishingposter-previews",
    "SPACES_POSTERS_BUCKET": "fishingposter-posters",
}

_FORMAT_TO_FMT: Final[dict[str, Literal["JPEG", "PNG"]]] = {
    "jpeg": "JPEG",
    "png": "PNG",
}

_FORMAT_TO_CT: Final[dict[str, str]] = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


# ---------------------------------------------------------------------------
# DB-backed cache (Phase 6 polish)
# ---------------------------------------------------------------------------
_CACHE_TTL_SEC: Final[int] = 300

_cache_lock = threading.Lock()
_cache: dict[int, tuple[float, TierConfig]] = {}


def reset_cache() -> None:
    """Drop the in-memory cache so the next call re-queries the DB.

    Called from the admin save handler and from tests.
    """
    with _cache_lock:
        _cache.clear()


def _row_to_config(row: object, tier: int) -> TierConfig:
    """Convert a ``RenderPreset`` ORM row to a :class:`TierConfig`."""
    fmt_raw = str(getattr(row, "format", "jpeg")).lower()
    fmt = _FORMAT_TO_FMT.get(fmt_raw, "JPEG")
    bucket_env = str(getattr(row, "bucket_env_var", _baseline(tier).bucket_env))
    default_bucket = _DEFAULT_BUCKETS.get(bucket_env, _baseline(tier).default_bucket)
    return TierConfig(
        tier=int(getattr(row, "tier", tier)),
        long_edge_px=int(row.long_edge_px),  # type: ignore[attr-defined]
        dpi=int(row.dpi),  # type: ignore[attr-defined]
        fmt=fmt,
        jpeg_quality=int(getattr(row, "jpeg_quality", 0) or 0),
        watermark=bool(getattr(row, "watermark_enabled", False)),
        public=bool(getattr(row, "public_read", False)),
        bucket_env=bucket_env,
        default_bucket=default_bucket,
        content_type=_FORMAT_TO_CT.get(fmt_raw, "image/jpeg"),
    )


def _baseline(tier: int) -> TierConfig:
    """Hardcoded baseline for ``tier`` — used as DB fallback."""
    cfg = TIER_CONFIG.get(tier)
    if cfg is None:
        raise ValueError(
            f"Unknown tier {tier!r}; valid: {sorted(TIER_CONFIG.keys())!r}"
        )
    return cfg


def _load_from_db(tier: int) -> TierConfig | None:
    """Query the ``render_presets`` table. Return None if the DB is unavailable."""
    try:
        from sqlalchemy import select

        from review_app.db import get_session
        from review_app.render.presets_model import RenderPreset
    except ImportError:
        return None
    try:
        with get_session() as session:
            row = session.execute(
                select(RenderPreset).where(RenderPreset.tier == tier)
            ).scalar_one_or_none()
            if row is None:
                return None
            return _row_to_config(row, tier)
    except Exception as exc:  # pragma: no cover - DB failures degrade to fallback
        logger.debug("render presets DB load failed (tier=%d): %s", tier, exc)
        return None


def get_tier_config(tier: int) -> TierConfig:
    """Return the :class:`TierConfig` for ``tier``.

    Looks up the live ``render_presets`` row first (cached for 5 minutes
    in-memory) and falls back to the hardcoded :data:`TIER_CONFIG` if
    the DB row is missing or the DB is unavailable.

    Raises ``ValueError`` for an unknown tier.
    """
    if tier not in TIER_CONFIG:
        raise ValueError(
            f"Unknown tier {tier!r}; valid: {sorted(TIER_CONFIG.keys())!r}"
        )

    now = time.time()
    with _cache_lock:
        existing = _cache.get(tier)
        if existing and (now - existing[0]) < _CACHE_TTL_SEC:
            return existing[1]

    db_cfg = _load_from_db(tier)
    cfg = db_cfg if db_cfg is not None else _baseline(tier)

    # Preserve the baseline's content_type if the DB row didn't supply a
    # PNG/JPEG fmt match (defensive — we already mapped above).
    if not cfg.content_type:
        cfg = replace(cfg, content_type=_baseline(tier).content_type)

    with _cache_lock:
        _cache[tier] = (time.time(), cfg)
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
    "reset_cache",
]
