"""ReprintRequest ORM model (mirrors alembic 0017_reprints).

Lives in the refunds package because the customer-facing UX bundles refunds
and reprints into a single "something went wrong with my order" flow even
though the backend operations are different (reprint creates a NEW Prodigi
order; refund cancels payment).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, uuid7

if TYPE_CHECKING:
    pass


VALID_REPRINT_STATUSES: frozenset[str] = frozenset(
    {"pending", "approved", "rejected", "completed"}
)
VALID_REPRINT_ROLES: frozenset[str] = frozenset({"customer", "admin"})


class ReprintRequest(Base):
    """Customer- or admin-initiated reprint request."""

    __tablename__ = "reprint_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "orders.id",
            ondelete="RESTRICT",
            name="fk_reprint_requests_order_id_orders",
        ),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customers.id",
            ondelete="RESTRICT",
            name="fk_reprint_requests_customer_id_customers",
        ),
        nullable=False,
    )
    requested_by_role: Mapped[str] = mapped_column(Text, nullable=False)
    line_item_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_paid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    new_prodigi_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_reprint_requests_decided_by_user_id_users",
        ),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "requested_by_role IN ('customer','admin')",
            name="ck_reprint_requests_requested_by_role",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','completed')",
            name="ck_reprint_requests_status",
        ),
        Index("ix_reprint_requests_order_id", "order_id"),
        Index("ix_reprint_requests_customer_id", "customer_id"),
        Index(
            "ix_reprint_requests_status_created_at", "status", "created_at"
        ),
    )


__all__ = ["VALID_REPRINT_ROLES", "VALID_REPRINT_STATUSES", "ReprintRequest"]
