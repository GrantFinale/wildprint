"""Cron scheduling for wildprint background jobs (Phase 5a).

Wraps ``rq-scheduler`` so the rest of the app can register recurring jobs
without touching Redis directly. The scheduler runs as a SEPARATE process
from the web container (see ``scripts/run_scheduler.py`` and
``Dockerfile.scheduler``); web/worker containers should never instantiate
a Scheduler — they only enqueue.

Public surface:

* :func:`get_scheduler` — lazy singleton :class:`rq_scheduler.Scheduler`.
* :func:`enqueue_now` — enqueue a one-shot job to run immediately. Useful
  for the admin "fire now" button (see ``flask cron-fire``).
* :func:`reset_for_tests` — drop the singleton (tests).

Why rq-scheduler over APScheduler:
  * We already pay the RQ + Redis cost; rq-scheduler reuses both.
  * Jobs land in the same Redis queue the worker process drains, so the
    worker container needs no scheduler-specific code path.
  * APScheduler's "in-memory or APS-managed-DB" model is awkward for a
    multi-container deploy.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final

from review_app.queue import QUEUE_DEFAULT, get_queue, get_redis

if TYPE_CHECKING:
    from collections.abc import Callable

    from rq.job import Job
    from rq_scheduler import Scheduler


logger = logging.getLogger(__name__)


_scheduler_singleton: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """Return (and cache) a process-wide :class:`rq_scheduler.Scheduler`.

    Uses the same Redis connection as the RQ queue and binds to the
    ``default`` queue (where workers pick jobs up).
    """
    global _scheduler_singleton
    if _scheduler_singleton is None:
        from rq_scheduler import Scheduler

        _scheduler_singleton = Scheduler(
            queue=get_queue(QUEUE_DEFAULT),
            connection=get_redis(),
        )
    return _scheduler_singleton


def reset_for_tests() -> None:
    """Drop the cached scheduler singleton (for tests)."""
    global _scheduler_singleton
    _scheduler_singleton = None


def enqueue_now(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
    """Enqueue a one-shot job for immediate execution.

    Bypasses the scheduler — goes straight on the default queue. Returns
    the RQ Job so the caller can poll status if needed.
    """
    from review_app.queue import enqueue

    return enqueue(func, *args, **kwargs)


# Re-export for convenience.
__all__ = [
    "DEFAULT_SCHEDULER_INTERVAL_SECONDS",
    "enqueue_now",
    "get_scheduler",
    "reset_for_tests",
]


# How often the scheduler loop wakes up to enqueue due jobs. 30s matches
# the most-frequent recurring job (drain_outbox_job).
DEFAULT_SCHEDULER_INTERVAL_SECONDS: Final[int] = int(
    os.environ.get("SCHEDULER_INTERVAL_SECONDS", "30")
)
