"""New cron jobs introduced in Phase 5a.

Existing recurring jobs (``drain_outbox_job``, ``refresh_all_skus_job``)
live in their feature modules already. This module owns the jobs that
didn't exist before Phase 5a:

* :func:`cleanup_old_render_outputs` — weekly tier-1/2 cleanup.
* :func:`monitor_failed_callbacks` — alert on stuck Prodigi callbacks.
* :func:`monitor_dead_outbox` — alert on dead outbox rows.

Job functions must be importable by the worker process (no closures, no
methods bound to per-request state) and should keep their imports late so
the scheduler container doesn't need every dependency.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Render cleanup
# ---------------------------------------------------------------------------
def cleanup_old_render_outputs(
    older_than_days: int = 90,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete tier-1 / tier-2 render outputs older than N days from Spaces + DB.

    Tier-3 (high-res print files) are protected — they're retained for 18
    months per the project retention policy and are dropped by a separate
    job (not scheduled by default).

    Returns a small dict so RQ result inspection / Sentry breadcrumbs see
    progress without parsing logs.
    """
    from sqlalchemy import select

    from review_app.db import get_session
    from review_app.render.db_models import RenderOutputRow
    from review_app.storage import spaces

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    deleted_db = 0
    deleted_spaces = 0
    spaces_errors = 0

    with get_session() as session:
        stmt = (
            select(RenderOutputRow)
            .where(RenderOutputRow.tier.in_([1, 2]))
            .where(RenderOutputRow.generated_at < cutoff)
        )
        rows = list(session.execute(stmt).scalars().all())

        for row in rows:
            if not dry_run:
                try:
                    spaces.delete_object(
                        bucket=row.storage_bucket,
                        key=row.storage_key,
                    )
                    deleted_spaces += 1
                except Exception as exc:  # broad: any storage failure
                    spaces_errors += 1
                    logger.warning(
                        "cleanup_render_outputs: spaces delete failed bucket=%s key=%s err=%s",
                        row.storage_bucket,
                        row.storage_key,
                        exc,
                    )
                session.delete(row)
                deleted_db += 1
        if not dry_run:
            session.commit()

    result = {
        "cutoff": cutoff.isoformat(),
        "candidates": len(rows),
        "deleted_db": deleted_db,
        "deleted_spaces": deleted_spaces,
        "spaces_errors": spaces_errors,
        "dry_run": dry_run,
    }
    logger.info("cleanup_render_outputs: %s", result)
    return result


# ---------------------------------------------------------------------------
# 2. Failed Prodigi callbacks monitor
# ---------------------------------------------------------------------------
def monitor_failed_callbacks(
    *,
    stale_after_seconds: int = 60 * 60,
) -> dict[str, Any]:
    """Find Prodigi callbacks stuck in error > stale_after_seconds. Alert via Sentry.

    Phase 5a only the alert path is exercised — the recovery action
    (re-process / dead-letter) is left to the operator. The job's job is
    to make sure stuck rows get human attention.
    """
    from sqlalchemy import func, select

    from review_app.db import get_session
    from review_app.prodigi.db_models import ProdigiCallback

    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)

    with get_session() as session:
        stmt = (
            select(func.count())
            .select_from(ProdigiCallback)
            .where(ProdigiCallback.processed_status == "error")
            .where(ProdigiCallback.received_at < cutoff)
        )
        stale_count = int(session.execute(stmt).scalar_one() or 0)

    if stale_count:
        msg = (
            f"prodigi_callbacks stuck in error: {stale_count} rows older "
            f"than {stale_after_seconds}s"
        )
        logger.error(msg)
        _emit_sentry(msg, level="error", tags={"job": "monitor_failed_callbacks"})

    return {
        "stale_count": stale_count,
        "cutoff": cutoff.isoformat(),
        "alerted": stale_count > 0,
    }


# ---------------------------------------------------------------------------
# 3. Dead outbox monitor
# ---------------------------------------------------------------------------
def monitor_dead_outbox() -> dict[str, Any]:
    """Find outbox rows that have hit ``status='dead'``. Alert via Sentry."""
    from sqlalchemy import func, select

    from review_app.db import get_session
    from review_app.email.outbox import STATUS_DEAD, OutboxEntry

    with get_session() as session:
        stmt = (
            select(func.count())
            .select_from(OutboxEntry)
            .where(OutboxEntry.status == STATUS_DEAD)
        )
        dead_count = int(session.execute(stmt).scalar_one() or 0)

    if dead_count:
        msg = f"outbox has {dead_count} dead rows — manual review required"
        logger.error(msg)
        _emit_sentry(msg, level="error", tags={"job": "monitor_dead_outbox"})

    return {"dead_count": dead_count, "alerted": dead_count > 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _emit_sentry(
    msg: str,
    *,
    level: str = "warning",
    tags: dict[str, str] | None = None,
) -> None:
    """Best-effort Sentry alert. No-op when Sentry isn't installed/configured."""
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - sentry-sdk in requirements
        return
    try:
        with sentry_sdk.push_scope() as scope:
            for k, v in (tags or {}).items():
                scope.set_tag(k, v)
            sentry_sdk.capture_message(msg, level=level)
    except Exception:  # pragma: no cover - never let monitor jobs crash
        pass


__all__ = [
    "cleanup_old_render_outputs",
    "monitor_dead_outbox",
    "monitor_failed_callbacks",
]
