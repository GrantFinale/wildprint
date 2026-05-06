"""SQLAlchemy model + repository helpers for the `outbox` table.

Mirrors the schema in alembic/versions/0004_outbox.py. The model is imported
by alembic/env.py so autogenerate diffs match the declaration.

Repository functions:
- :func:`enqueue`     — write a new pending row (called inside a request TX).
- :func:`claim_batch` — worker grabs a batch of due rows with FOR UPDATE
  SKIP LOCKED, marks them ``sending``.
- :func:`mark_sent`   — terminal success.
- :func:`mark_failed` — increment attempts; either schedule a retry or flip
  to terminal ``dead`` if the cap is exhausted.

Backoff schedule (after attempt N completes, 1-indexed):
    1 →  1 minute
    2 →  5 minutes
    3 → 25 minutes
    4 →  2 hours
    5 → 10 hours

This is roughly exponential (geometric ratio ~5x) and reaches ~half a day
of retry budget before declaring a row dead, which is enough to ride out
typical Resend/SES upstream incidents.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

# SQLite treats `INTEGER PRIMARY KEY` as a rowid alias (auto-increments);
# it does NOT do the same for `BIGINT PRIMARY KEY`. Use a dialect variant
# so Postgres still gets a real BIGINT while SQLite tests work without
# manual id assignment.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

from review_app.db.base import Base

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Status enum (string-typed; Postgres uses CHECK constraint, not native ENUM)
# ---------------------------------------------------------------------------
STATUS_PENDING: Final[str] = "pending"
STATUS_SENDING: Final[str] = "sending"
STATUS_SENT: Final[str] = "sent"
STATUS_FAILED: Final[str] = "failed"
STATUS_DEAD: Final[str] = "dead"

VALID_STATUSES: Final[tuple[str, ...]] = (
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_FAILED,
    STATUS_DEAD,
)

# Retry backoff schedule. Index = attempt count just completed (1..N).
# index 0 is unused (we never call this with attempts=0 post-bump).
_BACKOFF_SCHEDULE: Final[tuple[timedelta, ...]] = (
    timedelta(minutes=1),    # after attempt 1
    timedelta(minutes=5),    # after attempt 2
    timedelta(minutes=25),   # after attempt 3
    timedelta(hours=2),      # after attempt 4
    timedelta(hours=10),     # after attempt 5
)


def _backoff_for(attempts: int) -> timedelta:
    """Return the delay until the next retry after the Nth attempt.

    Attempts beyond the schedule cap reuse the longest delay.
    """
    if attempts <= 0:
        return _BACKOFF_SCHEDULE[0]
    idx = min(attempts - 1, len(_BACKOFF_SCHEDULE) - 1)
    return _BACKOFF_SCHEDULE[idx]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class OutboxEntry(Base):
    """One row per pending/in-flight/completed async side effect.

    NOT timestamp-mixed-in: we manage `updated_at` explicitly in the worker
    transitions below so they're an atomic part of the same UPDATE that
    flips status / increments attempts.
    """

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(
        _BigIntPK,
        primary_key=True,
        autoincrement=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("5"),
    )
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'dead')",
            name="outbox_status_in_enum",
        ),
        Index("outbox_status_next_retry_ix", "status", "next_retry_at"),
        Index("outbox_kind_created_ix", "kind", text("created_at DESC")),
    )

    def __repr__(self) -> str:
        return (
            f"<OutboxEntry id={self.id} kind={self.kind!r} "
            f"status={self.status!r} attempts={self.attempts}>"
        )


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------
def enqueue(
    session: "Session",
    *,
    kind: str,
    to: str,
    payload: dict[str, Any],
    max_attempts: int = 5,
) -> OutboxEntry:
    """Insert a new pending outbox row in the caller's transaction.

    The `to` address is folded into the payload under ``payload["to"]`` so
    the worker has everything it needs to render+send from a single column.
    Storing it inline (vs. a dedicated column) keeps the table generic
    across kinds — Phase 1+ can add `prodigi.create_order` rows whose
    "recipient" is a Prodigi sku, not an email address.

    Does NOT commit. Caller commits the surrounding transaction.
    """
    if max_attempts <= 0:
        raise ValueError(f"max_attempts must be positive, got {max_attempts!r}")
    merged_payload: dict[str, Any] = {"to": to, **payload}
    entry = OutboxEntry(
        kind=kind,
        payload=merged_payload,
        status=STATUS_PENDING,
        attempts=0,
        max_attempts=max_attempts,
        next_retry_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(entry)
    return entry


def claim_batch(session: "Session", limit: int = 10) -> list[OutboxEntry]:
    """Atomically claim up to ``limit`` due rows for processing.

    Selects rows where status IN ('pending','failed') and next_retry_at <=
    now(), locks them with ``FOR UPDATE SKIP LOCKED`` (so concurrent workers
    don't claim the same rows), and marks them ``sending``. The caller
    flushes/commits to release the locks; subsequent calls to
    :func:`mark_sent` / :func:`mark_failed` finalize each row.

    On SQLite (test environment), ``FOR UPDATE SKIP LOCKED`` is silently
    ignored — concurrency tests against SQLite must use serial assertions
    rather than truly parallel sessions.
    """
    if limit <= 0:
        return []

    now = datetime.now(UTC)

    bind = session.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    stmt = (
        select(OutboxEntry)
        .where(OutboxEntry.status.in_([STATUS_PENDING, STATUS_FAILED]))
        .where(OutboxEntry.next_retry_at <= now)
        .order_by(OutboxEntry.next_retry_at.asc())
        .limit(limit)
    )
    if is_pg:
        # SKIP LOCKED is the whole point of the outbox concurrency story.
        stmt = stmt.with_for_update(skip_locked=True)

    rows: list[OutboxEntry] = list(session.execute(stmt).scalars().all())

    for row in rows:
        row.status = STATUS_SENDING
        row.updated_at = now

    # Flush so the SENDING status is visible to other workers immediately
    # (and so the FOR UPDATE locks are released by the implicit txn commit
    # that the caller will perform).
    session.flush()
    return rows


def mark_sent(session: "Session", entry_id: int, message_id: str) -> None:
    """Terminal success — flip status=sent, stamp sent_at, record provider id.

    The provider message id is appended to the payload under
    ``payload["resend_message_id"]`` so we have a permanent forensics trail
    without adding a new column.
    """
    entry = session.get(OutboxEntry, entry_id)
    if entry is None:
        raise LookupError(f"OutboxEntry id={entry_id} not found")
    now = datetime.now(UTC)
    entry.status = STATUS_SENT
    entry.sent_at = now
    entry.updated_at = now
    entry.last_error = None
    # Mutating a JSON column requires a fresh dict assignment for SQLAlchemy
    # to pick up the change (it diffs by identity, not by deep equality).
    new_payload: dict[str, Any] = dict(entry.payload)
    new_payload["resend_message_id"] = message_id
    entry.payload = new_payload
    session.flush()


def mark_failed(session: "Session", entry_id: int, error: str) -> None:
    """Increment attempts; schedule next retry or flip to ``dead``.

    Schedule:
      attempts <  max_attempts → status=failed, next_retry_at += backoff
      attempts >= max_attempts → status=dead (terminal; worker stops touching it)
    """
    entry = session.get(OutboxEntry, entry_id)
    if entry is None:
        raise LookupError(f"OutboxEntry id={entry_id} not found")
    now = datetime.now(UTC)
    entry.attempts += 1
    entry.last_error = error
    entry.updated_at = now
    if entry.attempts >= entry.max_attempts:
        entry.status = STATUS_DEAD
    else:
        entry.status = STATUS_FAILED
        entry.next_retry_at = now + _backoff_for(entry.attempts)
    session.flush()


__all__ = [
    "STATUS_DEAD",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_SENDING",
    "STATUS_SENT",
    "VALID_STATUSES",
    "OutboxEntry",
    "claim_batch",
    "enqueue",
    "mark_failed",
    "mark_sent",
]
