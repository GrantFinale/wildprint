"""Cron scheduler entry point.

Run with::

    python -m scripts.run_scheduler

Boots the rq-scheduler loop, registers every recurring job from
:mod:`review_app.scheduler.cron`, and runs forever.

Coolify deploys this as a separate service alongside web + worker. The
scheduler container needs:

* ``REDIS_URL`` — same value the web/worker containers use.
* ``DATABASE_URL`` — for jobs that touch DB (cleanup, monitors).
* Same secrets as the worker for any service the jobs call.

Process model: single process, single thread. The scheduler does NOT
execute job functions itself — it enqueues them on the Redis ``default``
queue, where the worker container picks them up. The interval below is
how often the scheduler wakes to check "is anything due?".
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType
from typing import Any, Optional

from review_app.scheduler import (
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    get_scheduler,
)
from review_app.scheduler.cron import setup_cron_jobs

# Optional: structlog if available (matches the worker's behaviour).
try:  # pragma: no cover - import guard
    import structlog

    _structlog_available = True
except ImportError:  # pragma: no cover - import guard
    structlog = None  # type: ignore[assignment]
    _structlog_available = False


def _build_logger() -> Any:
    if _structlog_available:
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
        )
        return structlog.get_logger("scripts.run_scheduler")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("scripts.run_scheduler")


def main() -> int:
    log = _build_logger()
    scheduler = get_scheduler()

    registered = setup_cron_jobs(scheduler)
    if _structlog_available:
        log.info("scheduler.start", jobs=list(registered.keys()))
    else:
        log.info("scheduler start, jobs=%s", list(registered.keys()))

    # Warm shutdown — same pattern as the RQ worker.
    def _on_signal(signum: int, _frame: Optional[FrameType]) -> None:
        if _structlog_available:
            log.info("scheduler.stop", signum=signum)
        else:
            log.info("scheduler stop signum=%s", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Block forever — rq-scheduler's ``run()`` enters its own loop.
    scheduler.run(interval=DEFAULT_SCHEDULER_INTERVAL_SECONDS)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
