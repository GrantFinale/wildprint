"""SQLAlchemy ORM model for the ``stripe_events`` dedup table.

Mirrors ``alembic/versions/0014_stripe_events.py``. Imported by both the
webhook handler and the alembic env so autogenerate sees the schema.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base

# SQLite treats `INTEGER PRIMARY KEY` as a rowid alias; BIGINT does not auto-
# increment on SQLite. Use a dialect variant so Postgres still gets BIGINT
# and SQLite tests work without manual id assignment.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def _json_col() -> Any:
    return JSON().with_variant(JSONB(), "postgresql")


VALID_PROCESSED_STATUSES: frozenset[str] = frozenset({"ok", "error"})


class StripeEvent(Base):
    """One row per Stripe webhook event we have seen.

    ``event_id`` is UNIQUE — duplicate webhook deliveries are recognized and
    return 200 immediately. ``raw_payload`` is the verified event JSON for
    forensics + replay. ``processed_at`` + ``processed_status`` reflect
    whether our handler ran cleanly.
    """

    __tablename__ = "stripe_events"

    id: Mapped[int] = mapped_column(
        _BigIntPK, primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(_json_col(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_stripe_events_event_id"),
        CheckConstraint(
            "processed_status IS NULL OR processed_status IN ('ok','error')",
            name="ck_stripe_events_processed_status",
        ),
        Index("ix_stripe_events_event_type", "event_type"),
        Index("ix_stripe_events_received_at", "received_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<StripeEvent event_id={self.event_id!r} type={self.event_type!r} "
            f"status={self.processed_status!r}>"
        )


__all__ = ["VALID_PROCESSED_STATUSES", "StripeEvent"]
