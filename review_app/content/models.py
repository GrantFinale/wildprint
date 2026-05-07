"""ContentBlock ORM model (mirrors alembic 0018_content_blocks)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base

if TYPE_CHECKING:
    pass


class ContentBlock(Base):
    """Editable content block (marketing slot OR email template piece)."""

    __tablename__ = "content_blocks"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    slot: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_content_blocks_updated_by_user_id_users",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (Index("ix_content_blocks_slot", "slot"),)


__all__ = ["ContentBlock"]
