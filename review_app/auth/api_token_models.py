"""SQLAlchemy ORM model for ``user_api_tokens`` (Phase 6 polish).

Mirrors the schema in alembic/versions/0025_user_2fa_and_tokens.py.

Tokens are stored as SHA-256 hex digests in ``token_hash``; the plaintext
is shown to the user exactly once at creation time. ``scopes`` is a JSON
string of capability strings.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, uuid7


class UserApiToken(Base):
    """One row per programmatic API token issued to an admin user."""

    __tablename__ = "user_api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'[]'"),
        default="[]",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_user_api_tokens_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<UserApiToken {self.name!r} user_id={self.user_id}>"


__all__ = ["UserApiToken"]
