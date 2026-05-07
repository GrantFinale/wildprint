"""Refund ORM model.

Mirrors ``alembic/versions/0012_refunds.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_app.db.base import Base, TimestampMixin, uuid7

if TYPE_CHECKING:
    from review_app.orders.models import Order


VALID_REFUND_STATUSES: frozenset[str] = frozenset(
    {"pending", "succeeded", "failed", "cancelled"}
)


class Refund(Base, TimestampMixin):
    """One refund attempt for an Order.

    A single Order can have multiple Refund rows (partial refunds, retries
    after a failed Stripe call, etc.).
    """

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "orders.id",
            ondelete="RESTRICT",
            name="fk_refunds_order_id_orders",
        ),
        nullable=False,
    )
    stripe_refund_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
        default="pending",
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_refunds_requested_by_user_id_users",
        ),
        nullable=True,
    )
    prodigi_cancel_attempted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
        default=False,
    )
    prodigi_cancel_succeeded: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    customer_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship("Order")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','succeeded','failed','cancelled')",
            name="ck_refunds_status",
        ),
        CheckConstraint(
            "amount_cents > 0", name="ck_refunds_amount_positive"
        ),
        UniqueConstraint(
            "stripe_refund_id", name="uq_refunds_stripe_refund_id"
        ),
        Index("ix_refunds_order_id", "order_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Refund order_id={self.order_id} amount_cents={self.amount_cents} "
            f"status={self.status!r}>"
        )


__all__ = ["VALID_REFUND_STATUSES", "Refund"]
