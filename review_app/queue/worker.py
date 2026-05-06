"""RQ worker entry point.

Run with:

    python -m review_app.queue.worker

Listens on `high`, `default`, `low` queues (in priority order). Handles
SIGTERM gracefully via RQ's built-in warm-shutdown machinery so Coolify
rolling deploys don't interrupt an in-flight tier-3 render.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType
from typing import Any, Optional

from rq import Worker

# Importing jobs here ensures their module is loaded in the worker process
# (RQ resolves callables by dotted path at execution time, but pre-importing
# fails fast on syntax / import errors at worker start instead of mid-job).
from review_app.queue import QUEUE_NAMES, get_queue, get_redis  # noqa: F401
from review_app.queue import jobs as _jobs  # noqa: F401

# Prefer structlog if it's already installed (Sub-task 0.9 wires it in
# project-wide); otherwise fall back to stdlib logging so this module works
# in isolation right now.
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
        return structlog.get_logger("review_app.queue.worker")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("review_app.queue.worker")


def main() -> int:
    log = _build_logger()
    connection = get_redis()
    queues = [get_queue(name) for name in QUEUE_NAMES]

    worker = Worker(queues, connection=connection)

    def _on_sigterm(signum: int, frame: Optional[FrameType]) -> None:
        # Delegate to RQ's warm shutdown: finish current job, then exit.
        log.info("worker.sigterm_received", signum=signum) if _structlog_available else log.info(
            "SIGTERM received (signum=%s); initiating warm shutdown", signum
        )
        worker.handle_warm_shutdown_request()

    signal.signal(signal.SIGTERM, _on_sigterm)
    # SIGINT (Ctrl-C in dev) — RQ already wires this, but installing our
    # handler keeps the log message consistent.
    signal.signal(signal.SIGINT, _on_sigterm)

    if _structlog_available:
        log.info(
            "worker.start",
            queues=[q.name for q in queues],
            redis=bool(connection),
        )
    else:
        log.info(
            "Starting RQ worker on queues=%s",
            [q.name for q in queues],
        )

    worker.work(with_scheduler=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
