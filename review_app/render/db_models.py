"""SQLAlchemy ORM models for ``render_specs`` and ``render_outputs``.

Mirrors the schema in ``alembic/versions/0007_render_specs.py``. Imported
from ``alembic/env.py`` so autogenerate sees these columns.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, UUIDPKMixin


def _json_col() -> Any:
    return JSON().with_variant(JSONB(), "postgresql")


def _uuid_col() -> Any:
    """Cross-dialect UUID column.

    SQLAlchemy 2.x ``Uuid`` natively round-trips :class:`uuid.UUID` objects on
    Postgres (via the native ``UUID`` type) and SQLite (via 32-char hex string
    storage). Avoids manual conversion at every query site.
    """
    return Uuid(as_uuid=True)


class RenderSpecRow(Base, UUIDPKMixin):
    """One row per canonical render recipe.

    The ``spec_hash`` is the cache key — a SHA-256 of the canonical JSON
    serialization of ``canonical_inputs`` (which itself includes
    ``renderer_version``).
    """

    __tablename__ = "render_specs"

    spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_inputs: Mapped[dict[str, Any]] = mapped_column(
        _json_col(), nullable=False
    )
    renderer_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("spec_hash", name="uq_render_specs_spec_hash"),
        Index("ix_render_specs_spec_hash", "spec_hash"),
    )


class RenderOutputRow(Base, UUIDPKMixin):
    """One row per (render_spec, tier). UNIQUE(spec_id, tier) is the lookup."""

    __tablename__ = "render_outputs"

    render_spec_id: Mapped[uuid.UUID] = mapped_column(
        _uuid_col(),
        ForeignKey("render_specs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    queue_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("tier IN (1, 2, 3)", name="ck_render_outputs_tier"),
        UniqueConstraint(
            "render_spec_id", "tier", name="uq_render_outputs_spec_tier"
        ),
        Index("ix_render_outputs_render_spec_id", "render_spec_id"),
        Index("ix_render_outputs_generated_at", "generated_at"),
    )


__all__ = ["RenderOutputRow", "RenderSpecRow"]
