"""Cache-aware tier render dispatcher.

* Tier 1 + Tier 2: synchronous render + upload + DB insert. Slow path is the
  first request; subsequent requests for the same spec are cache hits.
* Tier 3: enqueues an RQ job (the worker calls
  :func:`review_app.render.jobs.render_tier_job`). Returns the job id and a
  pending sentinel — caller polls.

Cache key = ``(render_spec.spec_hash, tier)`` via ``UNIQUE(render_spec_id,
tier)`` on ``render_outputs``. Hash collisions force regen by bumping
``renderer_version``.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from review_app.render.db_models import RenderOutputRow, RenderSpecRow
from review_app.render.renderer import MasterRenderer, render_tier
from review_app.render.spec import RenderSpec
from review_app.render.tiers import TIER_PRINT, get_tier_config
from review_app.storage.keys import preview_key, print_key, thumb_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_log = logging.getLogger(__name__)
_struct_log = structlog.get_logger("render.cache")


@dataclass(frozen=True, slots=True)
class TierResult:
    """Return value of :func:`get_or_render_tier`.

    For tier 1/2 (sync): ``url`` is set, ``job_id`` is None, ``pending`` False.
    For tier 3 cache miss: ``url`` is None, ``job_id`` is set, ``pending`` True.
    For tier 3 cache hit: ``url`` is the signed URL, ``pending`` False.
    """

    tier: int
    spec_hash: str
    url: str | None
    job_id: str | None = None
    pending: bool = False
    cached: bool = False
    bucket: str | None = None
    storage_key: str | None = None
    size_bytes: int | None = None


def _storage_key_for_tier(tier: int, spec_hash: str, *, order_id: str | None) -> str:
    if tier == 1:
        return thumb_key(spec_hash)
    if tier == 2:
        return preview_key(spec_hash)
    if tier == 3:
        if not order_id:
            raise ValueError("Tier 3 requires order_id for storage key construction")
        return print_key(order_id, spec_hash)
    raise ValueError(f"Unknown tier {tier!r}")


def _public_url(bucket: str, key: str) -> str:
    """Construct the canonical public Spaces URL for ``bucket/key``.

    DO Spaces public URL form: ``https://<bucket>.<region>.digitaloceanspaces.com/<key>``.
    Reads ``SPACES_ENDPOINT`` for the region host.
    """
    import os

    endpoint = os.environ.get("SPACES_ENDPOINT", "https://nyc3.digitaloceanspaces.com")
    # Convert https://nyc3.digitaloceanspaces.com -> nyc3.digitaloceanspaces.com
    host = endpoint.split("://", 1)[-1].rstrip("/")
    return f"https://{bucket}.{host}/{key}"


def _ensure_render_spec_row(
    session: Session, spec: RenderSpec
) -> RenderSpecRow:
    """Get-or-create a :class:`RenderSpecRow` for ``spec``."""
    spec_hash = spec.canonical_hash()
    existing = session.execute(
        select(RenderSpecRow).where(RenderSpecRow.spec_hash == spec_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = RenderSpecRow(
        spec_hash=spec_hash,
        canonical_inputs=spec.canonical_dict(),
        renderer_version=spec.renderer_version,
    )
    session.add(row)
    session.flush()  # populate row.id without committing the outer transaction
    return row


def _existing_output(
    session: Session, spec_row_id: Any, tier: int
) -> RenderOutputRow | None:
    return session.execute(
        select(RenderOutputRow).where(
            RenderOutputRow.render_spec_id == spec_row_id,
            RenderOutputRow.tier == tier,
        )
    ).scalar_one_or_none()


def _render_and_upload_sync(
    session: Session,
    spec: RenderSpec,
    tier: int,
    spec_row: RenderSpecRow,
    *,
    master_renderer: MasterRenderer | None,
    order_id: str | None,
) -> TierResult:
    """Perform a synchronous tier 1/2 render + upload + DB insert."""
    from review_app.storage import get_signed_url, put_object

    cfg = get_tier_config(tier)
    spec_hash = spec_row.spec_hash

    body = render_tier(spec, tier, master_renderer=master_renderer)
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

    out_row = RenderOutputRow(
        render_spec_id=spec_row.id,
        tier=tier,
        storage_bucket=bucket,
        storage_key=storage_key,
        file_size_bytes=len(body),
        content_hash=content_hash,
    )
    session.add(out_row)
    session.flush()

    if cfg.public:
        url = _public_url(bucket, storage_key)
    else:
        url = get_signed_url(bucket=bucket, key=storage_key, expires_in=3600)

    _struct_log.info(
        "render_tier cache_miss_synced",
        tier=tier,
        spec_hash=spec_hash[:12],
        bucket=bucket,
        size_bytes=len(body),
    )

    return TierResult(
        tier=tier,
        spec_hash=spec_hash,
        url=url,
        cached=False,
        bucket=bucket,
        storage_key=storage_key,
        size_bytes=len(body),
    )


def _enqueue_tier3(
    session: Session,
    spec: RenderSpec,
    spec_row: RenderSpecRow,
    *,
    order_id: str | None,
    queue_name: str,
    job_timeout: int,
) -> TierResult:
    """Enqueue a tier-3 render and stamp ``queue_job_id`` on a placeholder row."""
    from review_app.queue import enqueue
    from review_app.render.jobs import render_tier_job

    spec_hash = spec_row.spec_hash
    job = enqueue(
        render_tier_job,
        spec.model_dump(),
        TIER_PRINT,
        order_id,
        queue=queue_name,
        job_timeout=job_timeout,
    )

    _struct_log.info(
        "render_tier3 enqueued",
        spec_hash=spec_hash[:12],
        job_id=job.id,
        queue=queue_name,
    )

    return TierResult(
        tier=TIER_PRINT,
        spec_hash=spec_hash,
        url=None,
        job_id=job.id,
        pending=True,
        cached=False,
    )


def _hit_to_url(out_row: RenderOutputRow) -> str:
    """Convert a cached :class:`RenderOutputRow` to a fetchable URL."""
    from review_app.storage import get_signed_url

    cfg = get_tier_config(out_row.tier)
    if cfg.public:
        return _public_url(out_row.storage_bucket, out_row.storage_key)
    return get_signed_url(
        bucket=out_row.storage_bucket,
        key=out_row.storage_key,
        expires_in=3600,
    )


def get_or_render_tier(
    session: Session,
    spec: RenderSpec,
    tier: int,
    *,
    master_renderer: MasterRenderer | None = None,
    order_id: str | None = None,
    queue_name: str = "default",
    job_timeout: int = 600,
) -> TierResult:
    """Cache-aware tier render.

    Args:
        session: SQLAlchemy session. Caller owns transaction boundaries.
            The function ``flush()``es but does NOT commit.
        spec: the canonical render spec.
        tier: 1 (thumb), 2 (preview), or 3 (print).
        master_renderer: optional injection for the master-image producer.
            Tests pass a fake; production passes the default poster_layout
            wrapper.
        order_id: required for tier 3 (used in the storage key path).
        queue_name: tier-3 only — RQ queue name. Defaults to ``"default"``.
        job_timeout: tier-3 only — RQ timeout in seconds. Defaults to 600.

    Returns:
        :class:`TierResult` describing either the cached/fresh URL (tier 1/2
        or tier-3 hit) or the enqueued job id (tier-3 miss).
    """
    spec_row = _ensure_render_spec_row(session, spec)
    spec_hash = spec_row.spec_hash

    # ------------------------------------------------------------------ cache lookup
    existing = _existing_output(session, spec_row.id, tier)
    if existing is not None:
        url = _hit_to_url(existing)
        _struct_log.info(
            "render_tier cache_hit",
            tier=tier,
            spec_hash=spec_hash[:12],
            bucket=existing.storage_bucket,
        )
        return TierResult(
            tier=tier,
            spec_hash=spec_hash,
            url=url,
            cached=True,
            bucket=existing.storage_bucket,
            storage_key=existing.storage_key,
            size_bytes=existing.file_size_bytes,
        )

    # ------------------------------------------------------------------ miss
    if tier == TIER_PRINT:
        return _enqueue_tier3(
            session,
            spec,
            spec_row,
            order_id=order_id,
            queue_name=queue_name,
            job_timeout=job_timeout,
        )

    return _render_and_upload_sync(
        session,
        spec,
        tier,
        spec_row,
        master_renderer=master_renderer,
        order_id=order_id,
    )


__all__ = ["TierResult", "get_or_render_tier"]
