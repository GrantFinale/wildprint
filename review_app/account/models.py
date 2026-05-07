"""CustomerLoginToken ORM model (mirrors alembic 0016_customer_login_tokens)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, uuid7

if TYPE_CHECKING:
    pass


class CustomerLoginToken(Base):
    """A single-use magic-link login token for a customer."""

    __tablename__ = "customer_login_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customers.id",
            ondelete="CASCADE",
            name="fk_customer_login_tokens_customer_id_customers",
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "token_hash", name="uq_customer_login_tokens_token_hash"
        ),
        Index("ix_customer_login_tokens_customer_id", "customer_id"),
        Index("ix_customer_login_tokens_expires_at", "expires_at"),
    )


__all__ = ["CustomerLoginToken"]
