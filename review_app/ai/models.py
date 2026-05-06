"""SQLAlchemy model for the `ai_usage_log` telemetry table.

Mirrors the schema in alembic/versions/0003_ai_usage.py. Imported by
alembic/env.py via `review_app.ai.models` so autogenerate diffs match
this declaration.

This model intentionally does NOT use the UUIDPKMixin/TimestampMixin
helpers from `review_app.db.base` — telemetry is high-volume, append-only,
and gets its own BIGSERIAL id + a single created_at column managed by the
DB server_default. We don't want the ORM `before_flush` updated_at hook
firing on every AI call.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base


class AIUsageLog(Base):
    """One row per AI provider call (when AI_LOGGING_ENABLED=1)."""

    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    render_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        # No FK at the model level — the migration installs it
        # conditionally because render_specs may not exist yet in Phase 0.
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    tokens_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    units: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=4),
        nullable=True,
    )
    cost_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'error')",
            name="ai_usage_status_in_enum",
        ),
        Index("ai_usage_provider_created_ix", "provider", text("created_at DESC")),
        Index("ai_usage_created_ix", text("created_at DESC")),
        Index(
            "ai_usage_render_spec_ix",
            "render_spec_id",
            postgresql_where=text("render_spec_id IS NOT NULL"),
            sqlite_where=text("render_spec_id IS NOT NULL"),
        ),
    )


__all__ = ["AIUsageLog"]
