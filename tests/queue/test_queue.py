"""Smoke tests for review_app.queue (Phase 0.4).

Unit tests use fakeredis so they run in CI without a live Redis. The
integration test against a real Redis is gated behind
`@pytest.mark.integration` and only runs when `REDIS_URL` is set.
"""

from __future__ import annotations

import os
from typing import Iterator

import fakeredis
import pytest
from rq import SimpleWorker
from rq.job import Job, JobStatus

from review_app import queue as queue_module
from review_app.queue import QUEUE_DEFAULT, QUEUE_HIGH, QUEUE_LOW, enqueue, get_queue
from review_app.queue.jobs import ping_job


@pytest.fixture
def fake_redis() -> Iterator[fakeredis.FakeRedis]:
    """Fresh in-memory Redis for each test."""
    conn = fakeredis.FakeRedis()
    yield conn
    conn.flushall()
    queue_module.reset_for_tests()


def test_enqueue_returns_job(fake_redis: fakeredis.FakeRedis) -> None:
    job = enqueue(ping_job, "hello", connection=fake_redis)
    assert isinstance(job, Job)
    assert job.get_status() == JobStatus.QUEUED


def test_ping_job_executes_and_returns_dict(fake_redis: fakeredis.FakeRedis) -> None:
    job = enqueue(ping_job, "hello", connection=fake_redis)
    q = get_queue(QUEUE_DEFAULT, connection=fake_redis)

    worker = SimpleWorker([q], connection=fake_redis)
    worker.work(burst=True)

    job.refresh()
    assert job.get_status() == JobStatus.FINISHED
    result = job.return_value()
    assert isinstance(result, dict)
    assert result["echo"] == "hello"
    assert "ts" in result and result["ts"]  # ISO timestamp set


def test_ping_job_default_echo(fake_redis: fakeredis.FakeRedis) -> None:
    job = enqueue(ping_job, connection=fake_redis)
    q = get_queue(QUEUE_DEFAULT, connection=fake_redis)
    SimpleWorker([q], connection=fake_redis).work(burst=True)
    job.refresh()
    assert job.return_value() == {"echo": "pong", "ts": job.return_value()["ts"]}


def test_unknown_queue_name_rejected(fake_redis: fakeredis.FakeRedis) -> None:
    with pytest.raises(ValueError):
        enqueue(ping_job, queue="nonexistent", connection=fake_redis)


def test_high_and_low_queues_are_reserved(fake_redis: fakeredis.FakeRedis) -> None:
    # Should not raise — both queues are valid even though unused in Phase 0.
    enqueue(ping_job, queue=QUEUE_HIGH, connection=fake_redis)
    enqueue(ping_job, queue=QUEUE_LOW, connection=fake_redis)


# NOTE: the Phase 0.4 `render_print_job` stub was replaced in Phase 2 by the
# real tier-3 implementation in review_app/render/jobs.py. The stub-behavior
# tests are now covered by tests/render/test_*.py. We retain a single failed-
# queue smoke below using a deliberately-invalid render_print_job call.


def _always_fails(*_args: object, **_kwargs: object) -> None:
    """Helper job used to exercise the failed-queue path."""
    raise RuntimeError("intentional failure for failed-queue test")


def test_failed_job_lands_in_failed_queue(fake_redis: fakeredis.FakeRedis) -> None:
    """Acceptance criterion: failures land in the failed queue with a traceback."""
    job = enqueue(_always_fails, connection=fake_redis)
    q = get_queue(QUEUE_DEFAULT, connection=fake_redis)
    SimpleWorker([q], connection=fake_redis).work(burst=True)
    job.refresh()
    assert job.get_status() == JobStatus.FAILED
    assert job.exc_info is not None
    assert "RuntimeError" in job.exc_info
    assert "intentional failure" in job.exc_info


def test_module_import_does_not_require_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazy connection: importing the package must not crash without REDIS_URL."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    queue_module.reset_for_tests()
    # Import path must work; only get_redis() should fail.
    from review_app.queue import enqueue as _enqueue  # noqa: F401
    from review_app.queue.jobs import ping_job as _ping  # noqa: F401

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        queue_module.get_redis()


# ---------------------------------------------------------------------------
# Integration test — requires a real Redis at $REDIS_URL.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_redis_roundtrip() -> None:
    if not os.environ.get("REDIS_URL"):
        pytest.skip("REDIS_URL not set; skipping real-Redis integration test")
    queue_module.reset_for_tests()
    job = enqueue(ping_job, "real")
    q = get_queue(QUEUE_DEFAULT)
    SimpleWorker([q], connection=queue_module.get_redis()).work(burst=True)
    job.refresh()
    assert job.return_value()["echo"] == "real"
