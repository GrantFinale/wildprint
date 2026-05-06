"""Render queue scaffolding (Phase 0.4).

Lazy Redis connection + typed `enqueue` wrapper around RQ. The queue exists
primarily to handle Tier 3 high-res print renders triggered by Stripe
`payment_intent.succeeded` (see docs/integration-plan.md). Tier 1 + Tier 2
renders stay synchronous on the request thread.

This module deliberately does NOT touch Redis at import time so that the
Flask app can boot without `REDIS_URL` set (e.g. in unit tests, in
ad-hoc scripts, or when someone is just running the renderer locally).
"""

from __future__ import annotations

import os
from typing import Any, Callable, Final, Optional

from redis import Redis
from rq import Queue
from rq.job import Job

# Reserved queue names. `default` is the only queue actively used in
# Phase 0; `high` and `low` are reserved for future use (e.g. fast email
# jobs on `high`, slow analytics jobs on `low`).
QUEUE_DEFAULT: Final[str] = "default"
QUEUE_HIGH: Final[str] = "high"
QUEUE_LOW: Final[str] = "low"

QUEUE_NAMES: Final[tuple[str, ...]] = (QUEUE_HIGH, QUEUE_DEFAULT, QUEUE_LOW)

_redis_singleton: Optional[Redis] = None
_queues: dict[str, Queue] = {}


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL")
    if not url:
        raise RuntimeError(
            "REDIS_URL environment variable is not set; cannot connect to Redis. "
            "Set REDIS_URL=redis://host:port/db before enqueueing jobs."
        )
    return url


def get_redis() -> Redis:
    """Return a process-wide Redis connection, opening it lazily."""
    global _redis_singleton
    if _redis_singleton is None:
        _redis_singleton = Redis.from_url(_redis_url())
    return _redis_singleton


def get_queue(name: str = QUEUE_DEFAULT, *, connection: Optional[Redis] = None) -> Queue:
    """Return (and cache) the RQ Queue for `name`.

    `connection` is exposed primarily so tests can inject a fakeredis
    instance without monkey-patching the module-level singleton.
    """
    if name not in QUEUE_NAMES:
        raise ValueError(
            f"Unknown queue name {name!r}; valid: {QUEUE_NAMES!r}"
        )
    if connection is not None:
        # Don't cache injected (test) connections.
        return Queue(name, connection=connection)
    if name not in _queues:
        _queues[name] = Queue(name, connection=get_redis())
    return _queues[name]


def enqueue(
    func: Callable[..., Any],
    *args: Any,
    queue: str = QUEUE_DEFAULT,
    connection: Optional[Redis] = None,
    job_timeout: int = 600,
    **kwargs: Any,
) -> Job:
    """Enqueue `func(*args, **kwargs)` on the named queue and return the Job.

    Parameters
    ----------
    func:
        A picklable callable. RQ resolves this by import path on the worker
        side, so it must be importable from the worker process (not a lambda
        or a closure).
    queue:
        One of `QUEUE_NAMES`. Defaults to `default`.
    connection:
        Optional Redis connection override (for tests).
    job_timeout:
        Seconds before RQ kills the job. Default 10 min; tier-3 renders will
        bump this to ~300 s when they wire in (Phase 2/3).
    """
    q = get_queue(queue, connection=connection)
    return q.enqueue(func, *args, job_timeout=job_timeout, **kwargs)


def reset_for_tests() -> None:
    """Clear cached singletons; pytest fixtures call this between tests."""
    global _redis_singleton
    _redis_singleton = None
    _queues.clear()


__all__ = [
    "QUEUE_DEFAULT",
    "QUEUE_HIGH",
    "QUEUE_LOW",
    "QUEUE_NAMES",
    "enqueue",
    "get_queue",
    "get_redis",
    "reset_for_tests",
]
