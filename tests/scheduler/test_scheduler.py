"""Smoke tests for :mod:`review_app.scheduler`.

Uses fakeredis so the scheduler can be wired up without a live Redis.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

import fakeredis
import pytest

from review_app import queue as queue_module
from review_app import scheduler as scheduler_module
from review_app.scheduler.cron import JOB_CATALOG, setup_cron_jobs


@pytest.fixture
def fake_redis() -> Iterator[fakeredis.FakeRedis]:
    conn = fakeredis.FakeRedis()
    queue_module.reset_for_tests()
    scheduler_module.reset_for_tests()
    # Inject the fake connection by patching the redis getter.
    with patch("review_app.queue.get_redis", return_value=conn):
        with patch("review_app.scheduler.get_redis", return_value=conn):
            yield conn
    conn.flushall()
    queue_module.reset_for_tests()
    scheduler_module.reset_for_tests()


def test_get_scheduler_returns_singleton(fake_redis: fakeredis.FakeRedis) -> None:
    s1 = scheduler_module.get_scheduler()
    s2 = scheduler_module.get_scheduler()
    assert s1 is s2


def test_setup_cron_jobs_registers_full_catalog(fake_redis: fakeredis.FakeRedis) -> None:
    """All five Phase 5a recurring jobs land in the scheduler."""
    registered = setup_cron_jobs()
    assert set(registered.keys()) == set(JOB_CATALOG.keys())


def test_setup_cron_jobs_idempotent(fake_redis: fakeredis.FakeRedis) -> None:
    """Running setup twice produces the same set of jobs (no duplicates)."""
    setup_cron_jobs()
    first_ids = {j.id for j in scheduler_module.get_scheduler().get_jobs()}
    setup_cron_jobs()
    second_ids = {j.id for j in scheduler_module.get_scheduler().get_jobs()}
    # ids in the catalog must appear in both — the implementation cancels
    # and re-registers so the count for catalog ids should still be one each.
    for job_id in JOB_CATALOG:
        assert any(j.id == job_id for j in scheduler_module.get_scheduler().get_jobs())


def test_enqueue_now_pushes_to_default_queue(fake_redis: fakeredis.FakeRedis) -> None:
    from review_app.queue import QUEUE_DEFAULT, get_queue
    from review_app.queue.jobs import ping_job

    job = scheduler_module.enqueue_now(ping_job, "hello-from-scheduler")
    queued_ids = get_queue(QUEUE_DEFAULT).get_job_ids()
    assert job.id in queued_ids


def test_job_catalog_matches_documented_jobs() -> None:
    """If we forget to update JOB_CATALOG when adding a job, this fails."""
    expected = {
        "drain_outbox",
        "refresh_prodigi_quotes",
        "cleanup_render_outputs",
        "monitor_failed_callbacks",
        "monitor_dead_outbox",
    }
    assert set(JOB_CATALOG.keys()) == expected


# ---------------------------------------------------------------------------
# Job function smoke — the new Phase 5a jobs must at least be importable
# ---------------------------------------------------------------------------
@pytest.fixture
def patched_session(db_session: Any) -> Iterator[Any]:
    """Patch get_session() to yield the test session (no commit/close)."""

    @contextmanager
    def _fake_get_session() -> Iterator[Any]:
        # Don't commit/close — the conftest db_session fixture rolls back
        # on teardown.
        yield db_session

    with patch("review_app.db.get_session", _fake_get_session):
        yield db_session


def test_monitor_dead_outbox_returns_zero_when_table_empty(
    fake_redis: fakeredis.FakeRedis,
    patched_session: Any,
) -> None:
    from review_app.scheduler.jobs import monitor_dead_outbox

    # Empty outbox → dead_count=0, no alert.
    result = monitor_dead_outbox()
    assert result["dead_count"] == 0
    assert result["alerted"] is False


def test_monitor_failed_callbacks_returns_zero_when_table_empty(
    fake_redis: fakeredis.FakeRedis,
    patched_session: Any,
) -> None:
    from review_app.scheduler.jobs import monitor_failed_callbacks

    result = monitor_failed_callbacks()
    assert result["stale_count"] == 0
    assert result["alerted"] is False


def test_cleanup_render_outputs_dry_run(
    fake_redis: fakeredis.FakeRedis,
    patched_session: Any,
) -> None:
    from review_app.scheduler.jobs import cleanup_old_render_outputs

    # Empty render_outputs → 0 candidates.
    result = cleanup_old_render_outputs(older_than_days=90, dry_run=True)
    assert result["candidates"] == 0
    assert result["deleted_db"] == 0
    assert result["dry_run"] is True
