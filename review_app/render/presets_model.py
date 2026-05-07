"""SQLAlchemy model for ``render_presets`` — DB-backed tier configuration.

Mirrors the schema in alembic/versions/0023_render_presets.py. Imported by
alembic/env.py via ``review_app.render.presets_model`` so autogenerate
diffs match this declaration.

Phase 6 polish: replaces the hardcoded ``TIER_CONFIG`` dict with a small
3-row table so admins can tune render presets at runtime. The
:func:`review_app.render.tiers.get_tier_config` helper queries this table
on first call and caches the result for 5 minutes.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base

_UUID_FK = String(length=36)


class RenderPreset(Base):
    """One row per tier (1=thumb, 2=preview, 3=print)."""

    __tablename__ = "render_presets"

    tier: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        nullable=False,
    )
    long_edge_px: Mapped[int] = mapped_column(Integer, nullable=False)
    dpi: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    jpeg_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watermark_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    watermark_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    watermark_opacity: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=4, scale=3),
        nullable=True,
    )
    watermark_angle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bucket_env_var: Mapped[str] = mapped_column(Text, nullable=False)
    public_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID_FK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        CheckConstraint(
            "tier IN (1, 2, 3)",
            name="render_presets_tier_in_enum",
        ),
        CheckConstraint(
            "format IN ('jpeg', 'png', 'webp')",
            name="render_presets_format_in_enum",
        ),
        CheckConstraint(
            "long_edge_px BETWEEN 100 AND 12000",
            name="render_presets_long_edge_range",
        ),
        CheckConstraint(
            "dpi BETWEEN 72 AND 600",
            name="render_presets_dpi_range",
        ),
        CheckConstraint(
            "jpeg_quality IS NULL OR jpeg_quality BETWEEN 50 AND 95",
            name="render_presets_quality_range",
        ),
    )

    def __repr__(self) -> str:
        return f"<RenderPreset tier={self.tier} {self.long_edge_px}px {self.format}>"


__all__ = ["RenderPreset"]
