"""RQ job functions.

Job functions MUST be importable by the worker process (no lambdas, no
closures, no methods bound to per-request state). Keep them small and pure;
they should pull their own dependencies via well-known module paths.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def ping_job(echo: str = "pong") -> dict[str, str]:
    """No-op job that proves the queue works end-to-end.

    Returns a small dict so the test can assert the worker actually executed
    the function (vs. just enqueued it). The timestamp is UTC ISO 8601.
    """
    return {
        "echo": echo,
        "ts": datetime.now(UTC).isoformat(),
    }


# Tier-3 print render — Phase 2 implementation. We re-export from
# `review_app.render.jobs` so the worker can resolve this module path
# (`review_app.queue.jobs.render_print_job`) for jobs enqueued under the
# Phase 0/1 name, AND so `review_app.render.jobs.render_tier_job` is the
# canonical resolution path for newly-enqueued tier-3 work.
#
# Late-import inside a thin wrapper to avoid pulling Pillow + the renderer
# into processes that only enqueue (no need to drag the imaging stack into
# the web app to call `enqueue(render_print_job, ...)`).
def render_print_job(spec_dict: dict[str, Any], order_id: str) -> dict[str, Any]:
    """Tier-3 high-res print render. Phase 2 implementation.

    Delegates to :func:`review_app.render.jobs.render_print_job`. Kept under
    this module path for backward compatibility with any Phase 0/1 enqueues.
    """
    from review_app.render.jobs import render_print_job as _impl

    return _impl(spec_dict, order_id)


def render_tier_job(
    spec_dict: dict[str, Any],
    tier: int,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Generic tier render job — re-export of :func:`review_app.render.jobs.render_tier_job`."""
    from review_app.render.jobs import render_tier_job as _impl

    return _impl(spec_dict, tier, order_id=order_id)


def drain_outbox_job(batch_size: int = 10) -> dict[str, Any]:
    """Drain up to ``batch_size`` due rows from the `outbox` table.

    Phase 0.5 transactional outbox worker. Intended to run on a 30-second
    cron (e.g. RQ scheduler) once 0.11 staging or Phase 5 monitoring wires
    that up. Phase 0 just defines the job.

    Per-row flow:
      1. ``claim_batch()`` locks rows with FOR UPDATE SKIP LOCKED, flips
         them to status='sending', commits.
      2. For each claimed row, render templates from the stored payload,
         POST to Resend.
      3. On success, ``mark_sent()`` flips to status='sent' and stamps
         the Resend message id.
      4. On failure, ``mark_failed()`` increments attempts and either
         schedules a retry (status='failed' with backoff) or terminates
         (status='dead' once max_attempts is exhausted).

    Each step uses a fresh DB session so a crashed worker doesn't leave a
    long-running transaction open. Templates are re-rendered at send time
    (rather than at enqueue time) so a deploy that fixes a template bug
    can fix already-queued rows on next retry.

    Returns a small dict with counts so the caller / metrics layer can see
    progress without parsing logs.
    """
    # Late imports to avoid pulling DB / Jinja during a lightweight
    # `from review_app.queue.jobs import ping_job` in the test suite.
    from review_app.db import get_session
    from review_app.email import resend_client
    from review_app.email.outbox import (
        OutboxEntry,
        claim_batch,
        mark_failed,
        mark_sent,
    )
    from review_app.email.templates import render_subject, render_template

    if batch_size <= 0:
        return {"claimed": 0, "sent": 0, "failed": 0}

    # 1. Claim a batch in its own short transaction so the SELECT FOR UPDATE
    #    locks are released as soon as we've flipped status to 'sending'.
    claimed_ids: list[int] = []
    with get_session() as session:
        rows = claim_batch(session, limit=batch_size)
        claimed_ids = [r.id for r in rows]

    if not claimed_ids:
        return {"claimed": 0, "sent": 0, "failed": 0}

    sent = 0
    failed = 0
    for entry_id in claimed_ids:
        # Each row gets its own session/transaction so a single failure
        # doesn't roll back the rest of the batch.
        with get_session() as session:
            entry = session.get(OutboxEntry, entry_id)
            if entry is None:
                # Row was hard-deleted between claim and process — log and skip.
                logger.warning("drain_outbox: row %s vanished mid-flight", entry_id)
                continue

            # Email kinds get rendered + sent through Resend. Future kinds
            # (prodigi.create_order, etc.) will branch here.
            if not entry.kind.startswith("email."):
                mark_failed(
                    session,
                    entry_id,
                    f"Unsupported outbox kind for drain_outbox_job: {entry.kind!r}",
                )
                failed += 1
                continue

            payload: dict[str, Any] = dict(entry.payload)
            recipient = payload.pop("to", None)
            if not isinstance(recipient, str) or not recipient:
                mark_failed(
                    session,
                    entry_id,
                    "Missing or invalid 'to' field in outbox payload",
                )
                failed += 1
                continue

            try:
                subject = render_subject(entry.kind, payload)
                html, text = render_template(entry.kind, payload)
                message_id = resend_client.send_via_resend(
                    to=recipient,
                    subject=subject,
                    html=html,
                    text=text,
                )
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "drain_outbox: send failed for id=%s kind=%s — %s",
                    entry_id,
                    entry.kind,
                    error_msg,
                )
                mark_failed(session, entry_id, error_msg)
                failed += 1
                continue

            mark_sent(session, entry_id, message_id)
            logger.info(
                "drain_outbox: sent id=%s kind=%s resend_id=%s",
                entry_id,
                entry.kind,
                message_id,
            )
            sent += 1

    return {
        "claimed": len(claimed_ids),
        "sent": sent,
        "failed": failed,
    }


__all__ = ["drain_outbox_job", "ping_job", "render_print_job", "render_tier_job"]
