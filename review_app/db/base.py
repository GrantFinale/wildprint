"""SQLAlchemy 2.0 declarative base, naming convention, and common mixins.

Phase 0.2 scaffolding for the Prodigi integration. Other sub-tasks (auth,
ai-usage, orders, etc.) add their own model modules that import `Base` from
here. Keep this file dependency-free and import-safe — it must not require
`DATABASE_URL` to be set.
"""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
)

# Alembic-friendly constraint naming. Without this, autogenerate produces
# anonymous constraint names that diff noisily across environments.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Project-wide declarative base.

    All models in `review_app/**/models.py` should inherit from this class so
    that Alembic autogenerate sees them via `Base.metadata`.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# UUIDv7 helper
# ---------------------------------------------------------------------------
# Python stdlib doesn't ship UUIDv7 yet (added in 3.14, we're on 3.11). The
# `uuid_extensions` PyPI package is unmaintained. Implementation below follows
# RFC 9562 §5.7 (UUIDv7: 48-bit unix-ms timestamp + 4-bit version + 12-bit rand
# + 2-bit variant + 62-bit rand). UUIDv7 sorts lexicographically by creation
# time, which matches Postgres index locality much better than UUIDv4.
#
# Source: RFC 9562 — https://datatracker.ietf.org/doc/rfc9562/
def uuid7() -> uuid.UUID:
    """Return a new UUIDv7 (time-ordered random UUID).

    Application-side generator. Postgres has its own `uuidv7()` (pg17+) which
    we use as the column default in DDL when available; this helper exists for
    SQLite (tests) and for code paths that want to know the ID before INSERT.
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = secrets.randbits(12)  # 12 bits
    rand_b = secrets.randbits(62)  # 62 bits
    # Layout: ts(48) | ver(4)=0x7 | rand_a(12) | var(2)=0b10 | rand_b(62)
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------
class UUIDPKMixin:
    """Mixin providing a UUIDv7 primary key column named `id`.

    Default is generated client-side via `uuid7()`. When the deployed Postgres
    is on pg17+, individual model files can override `server_default` to call
    Postgres's native `uuidv7()` for slightly better locality.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )


class TimestampMixin:
    """Mixin providing `created_at` and `updated_at` timestamp columns.

    `updated_at` is maintained via SQLAlchemy ORM events rather than DB
    triggers — this keeps the schema portable to SQLite for tests and avoids
    the Postgres trigger boilerplate. Trade-off: bulk UPDATEs that bypass the
    ORM Session won't bump `updated_at`. That's acceptable for our workload.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


@event.listens_for(Session, "before_flush")
def _bump_updated_at(session: Session, flush_context: Any, instances: Any) -> None:
    """Touch `updated_at` on every dirty TimestampMixin instance pre-flush."""
    now = datetime.now(UTC)
    for obj in session.dirty:
        if isinstance(obj, TimestampMixin) and session.is_modified(obj, include_collections=False):
            obj.updated_at = now


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "TimestampMixin",
    "UUIDPKMixin",
    "uuid7",
]
