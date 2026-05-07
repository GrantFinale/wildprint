"""Cron job catalog — every recurring job + its cadence.

Lives separate from :mod:`review_app.scheduler.__init__` so the scheduler
module stays small and the catalog is easy to read top-to-bottom.

Job functions live in their feature modules (outbox lives in
:mod:`review_app.queue.jobs`, quote refresh in
:mod:`review_app.prodigi.quote_refresh`, etc.) — this module only handles
*scheduling*.

To schedule a NEW recurring job:

1. Add the job function in its feature module (must be importable by the
   worker process — no closures).
2. Add a registration line in :func:`setup_cron_jobs`.
3. Document it in ``docs/cron.md``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from review_app.scheduler import get_scheduler

if TYPE_CHECKING:
    from rq_scheduler import Scheduler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cadences (centralized so tests + docs read from the same constants)
# ---------------------------------------------------------------------------
DRAIN_OUTBOX_INTERVAL_SECONDS: Final[int] = 30
QUOTE_REFRESH_HOUR_UTC: Final[int] = 3
QUOTE_REFRESH_MINUTE_UTC: Final[int] = 0
CLEANUP_RENDERS_INTERVAL_SECONDS: Final[int] = 60 * 60 * 24 * 7  # 7 days
MONITOR_FAILED_CALLBACKS_INTERVAL_SECONDS: Final[int] = 60 * 15  # 15 min
MONITOR_DEAD_OUTBOX_INTERVAL_SECONDS: Final[int] = 60 * 30  # 30 min

# Render retention: tier 1 + 2 outputs are deleted after 90 days.
# Tier 3 (high-res print files) stay 18 months per the retention policy.
RENDER_TIER12_RETENTION_DAYS: Final[int] = 90


# ---------------------------------------------------------------------------
# Job catalog (id => description). The id is the unique key the scheduler
# uses to dedupe registrations across restarts.
# ---------------------------------------------------------------------------
JOB_CATALOG: Final[dict[str, str]] = {
    "drain_outbox": "Drain pending outbox rows (transactional email + side-effect fanout).",
    "refresh_prodigi_quotes": "Refresh Prodigi quote cache for every active SKU.",
    "cleanup_render_outputs": (
        "Delete tier-1/2 render outputs older than 90 days from Spaces + DB."
    ),
    "monitor_failed_callbacks": (
        "Alert on prodigi_callbacks rows stuck in error > 1 hour."
    ),
    "monitor_dead_outbox": "Alert on outbox rows in the dead state.",
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_cron_jobs(scheduler: Scheduler | None = None) -> dict[str, Any]:
    """Register every recurring job with the scheduler.

    Idempotent: if a job with the same id is already scheduled it is
    cancelled and re-registered. This handles deploys that change a
    cadence without leaving an orphan schedule.

    Returns a small dict of ``{id: scheduled_job}`` for caller logging.
    """
    if scheduler is None:
        scheduler = get_scheduler()

    # Cancel any existing schedule with our ids so we can re-register
    # without duplicate cron entries.
    for existing in list(scheduler.get_jobs()):
        existing_id = getattr(existing, "id", None) or ""
        if existing_id in JOB_CATALOG:
            try:
                scheduler.cancel(existing)
            except Exception:  # pragma: no cover - defensive
                logger.warning("scheduler.cancel failed for id=%s", existing_id)

    registered: dict[str, Any] = {}

    # Late imports — keep this module light so importing it from the admin
    # shell (e.g. for the "fire now" view) doesn't drag in the queue/render
    # stack unless we actually call ``setup_cron_jobs``.
    from review_app.prodigi.quote_refresh import refresh_all_skus_job
    from review_app.queue.jobs import drain_outbox_job
    from review_app.scheduler.jobs import (
        cleanup_old_render_outputs,
        monitor_dead_outbox,
        monitor_failed_callbacks,
    )

    # 1. Outbox drain — every 30s.
    registered["drain_outbox"] = scheduler.schedule(
        scheduled_time=datetime.now(UTC),
        func=drain_outbox_job,
        interval=DRAIN_OUTBOX_INTERVAL_SECONDS,
        repeat=None,
        id="drain_outbox",
        result_ttl=300,
        timeout=60,
    )

    # 2. Daily Prodigi quote refresh @ 03:00 UTC.
    next_quote_run = _next_daily_run(QUOTE_REFRESH_HOUR_UTC, QUOTE_REFRESH_MINUTE_UTC)
    registered["refresh_prodigi_quotes"] = scheduler.cron(
        cron_string=f"{QUOTE_REFRESH_MINUTE_UTC} {QUOTE_REFRESH_HOUR_UTC} * * *",
        func=refresh_all_skus_job,
        id="refresh_prodigi_quotes",
        timeout=1800,
        use_local_timezone=False,
    )
    logger.info(
        "scheduler: refresh_prodigi_quotes next run %s",
        next_quote_run.isoformat(),
    )

    # 3. Weekly cleanup of tier-1/2 render outputs.
    registered["cleanup_render_outputs"] = scheduler.schedule(
        scheduled_time=datetime.now(UTC) + timedelta(minutes=5),
        func=cleanup_old_render_outputs,
        kwargs={"older_than_days": RENDER_TIER12_RETENTION_DAYS},
        interval=CLEANUP_RENDERS_INTERVAL_SECONDS,
        repeat=None,
        id="cleanup_render_outputs",
        result_ttl=86400,
        timeout=900,
    )

    # 4. Failed Prodigi callbacks monitor — every 15 min.
    registered["monitor_failed_callbacks"] = scheduler.schedule(
        scheduled_time=datetime.now(UTC) + timedelta(minutes=1),
        func=monitor_failed_callbacks,
        interval=MONITOR_FAILED_CALLBACKS_INTERVAL_SECONDS,
        repeat=None,
        id="monitor_failed_callbacks",
        result_ttl=3600,
        timeout=120,
    )

    # 5. Dead outbox monitor — every 30 min.
    registered["monitor_dead_outbox"] = scheduler.schedule(
        scheduled_time=datetime.now(UTC) + timedelta(minutes=2),
        func=monitor_dead_outbox,
        interval=MONITOR_DEAD_OUTBOX_INTERVAL_SECONDS,
        repeat=None,
        id="monitor_dead_outbox",
        result_ttl=3600,
        timeout=120,
    )

    return registered


def _next_daily_run(hour_utc: int, minute_utc: int) -> datetime:
    """Return the next datetime when this hour:minute UTC will occur."""
    now = datetime.now(UTC)
    candidate = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def cancel_all() -> int:
    """Cancel every scheduled job in the catalog. Returns the count."""
    scheduler = get_scheduler()
    cancelled = 0
    for existing in list(scheduler.get_jobs()):
        existing_id = getattr(existing, "id", None) or ""
        if existing_id in JOB_CATALOG:
            try:
                scheduler.cancel(existing)
                cancelled += 1
            except Exception:  # pragma: no cover - defensive
                logger.warning("scheduler.cancel failed for id=%s", existing_id)
    return cancelled


__all__ = [
    "CLEANUP_RENDERS_INTERVAL_SECONDS",
    "DRAIN_OUTBOX_INTERVAL_SECONDS",
    "JOB_CATALOG",
    "MONITOR_DEAD_OUTBOX_INTERVAL_SECONDS",
    "MONITOR_FAILED_CALLBACKS_INTERVAL_SECONDS",
    "QUOTE_REFRESH_HOUR_UTC",
    "QUOTE_REFRESH_MINUTE_UTC",
    "RENDER_TIER12_RETENTION_DAYS",
    "cancel_all",
    "setup_cron_jobs",
]
