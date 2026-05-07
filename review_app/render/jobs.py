"""RQ job functions for the tier-3 render pipeline.

Tier 3 cannot run synchronously inside a Stripe webhook handler — a 7200x10800
PNG render takes ~30-90 s on the droplet, well past Stripe's 10 s budget.
:func:`render_tier_job` is the worker-side entry point invoked by
:func:`review_app.render.cache.get_or_render_tier`.

The job is registered with the RQ worker via the existing
``review_app.queue.jobs`` module (see ``__all__`` re-export there).
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import structlog

from review_app.render.renderer import render_tier
from review_app.render.spec import RenderSpec
from review_app.render.tiers import TIER_PRINT, get_tier_config

_log = logging.getLogger(__name__)
_struct_log = structlog.get_logger("render.jobs")


def render_tier_job(
    spec_dict: dict[str, Any],
    tier: int,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Render a tier from a serialized :class:`RenderSpec`, upload, persist.

    RQ-callable. Pickled args = ``(spec_dict, tier, order_id)``.

    Workflow:
      1. Hydrate :class:`RenderSpec` from ``spec_dict``.
      2. Open a fresh DB session (worker process — no Flask context).
      3. Render bytes via :func:`render_tier` (uses the default
         ``poster_layout`` master renderer).
      4. Upload to DO Spaces.
      5. Insert ``render_outputs`` row.
      6. Return a dict with ``url``, ``size_bytes``, ``render_ms``,
         ``content_hash``, ``spec_hash``.

    The job is intentionally pure-function-shaped so RQ can serialize it
    without closures. All dependencies are resolved by import.
    """
    # Late imports — keeps RQ's pickled-callable resolution snappy and
    # avoids dragging the DB engine into the test process when these
    # tests mock the job directly.
    from sqlalchemy import select

    from review_app.db import get_session
    from review_app.render.cache import _public_url, _storage_key_for_tier
    from review_app.render.db_models import RenderOutputRow, RenderSpecRow
    from review_app.storage import get_signed_url, put_object

    spec = RenderSpec(**spec_dict)
    spec_hash = spec.canonical_hash()
    cfg = get_tier_config(tier)

    t0 = time.perf_counter()
    body = render_tier(spec, tier)
    render_ms = int((time.perf_counter() - t0) * 1000)

    content_hash = hashlib.sha256(body).hexdigest()
    bucket = cfg.bucket()
    storage_key = _storage_key_for_tier(tier, spec_hash, order_id=order_id)

    put_object(
        bucket=bucket,
        key=storage_key,
        body=body,
        content_type=cfg.content_type,
        public=cfg.public,
        metadata={
            "spec-hash": spec_hash,
            "tier": str(tier),
            "renderer-version": spec.renderer_version,
        },
    )

    with get_session() as session:
        spec_row = session.execute(
            select(RenderSpecRow).where(RenderSpecRow.spec_hash == spec_hash)
        ).scalar_one_or_none()
        if spec_row is None:
            spec_row = RenderSpecRow(
                spec_hash=spec_hash,
                canonical_inputs=spec.canonical_dict(),
                renderer_version=spec.renderer_version,
            )
            session.add(spec_row)
            session.flush()

        out_row = RenderOutputRow(
            render_spec_id=spec_row.id,
            tier=tier,
            storage_bucket=bucket,
            storage_key=storage_key,
            file_size_bytes=len(body),
            content_hash=content_hash,
        )
        session.add(out_row)

    if cfg.public:
        url = _public_url(bucket, storage_key)
    else:
        url = get_signed_url(bucket=bucket, key=storage_key, expires_in=3600)

    _struct_log.info(
        "render_tier_job complete",
        tier=tier,
        spec_hash=spec_hash[:12],
        render_ms=render_ms,
        size_bytes=len(body),
        bucket=bucket,
    )

    return {
        "url": url,
        "size_bytes": len(body),
        "render_ms": render_ms,
        "content_hash": content_hash,
        "spec_hash": spec_hash,
        "tier": tier,
        "bucket": bucket,
        "storage_key": storage_key,
    }


def render_print_job(spec_dict: dict[str, Any], order_id: str) -> dict[str, Any]:
    """Tier-3 specialization of :func:`render_tier_job`.

    Replaces the ``render_print_job`` stub in ``review_app/queue/jobs.py``.
    Phase 2 keeps the old name available so any callers wired in Phase 0/1
    keep working — see ``review_app/queue/jobs.py`` for the re-export.
    """
    return render_tier_job(spec_dict, TIER_PRINT, order_id=order_id)


__all__ = ["render_print_job", "render_tier_job"]
