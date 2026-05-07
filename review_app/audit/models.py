"""SQLAlchemy ORM model for ``audit_log``.

Mirrors the schema in ``alembic/versions/0015_audit_log.py``. Imported by
``alembic/env.py`` so autogenerate diffs match the declaration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base

# Dialect-aware column types — mirror the migration helpers.
_BIGINT_PK = BigInteger().with_variant(Integer(), "sqlite")
_UUID_FK = UUID(as_uuid=True).with_variant(String(36), "sqlite")
_JSON = JSON().with_variant(JSONB(), "postgresql")
_IP = Text().with_variant(INET(), "postgresql")


class AuditLogEntry(Base):
    """One row per audited admin action.

    Append-only by convention — no UPDATE/DELETE happens through this
    model. The :func:`review_app.audit.record` helper is the only writer
    inside the app; manual deletions in dev are tolerated but discouraged
    (the table is small even at scale, ~2KB/row * 10 actions/day = trivial).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        _BIGINT_PK,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID_FK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(_IP, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index(
            "ix_audit_log_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_log_action_created",
            "action",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_log_target_created",
            "target_type",
            "target_id",
            text("created_at DESC"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLogEntry id={self.id} action={self.action!r} "
            f"target={self.target_type}/{self.target_id}>"
        )


__all__ = ["AuditLogEntry"]
