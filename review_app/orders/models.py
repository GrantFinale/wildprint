"""Order + OrderItem ORM models.

Mirrors ``alembic/versions/0011_orders.py``.

Money is represented as BIGINT cents throughout. Validate
``total_cents == subtotal_cents + shipping_cents + tax_cents`` at the
business-logic layer (the parallel agent's scope) — the DB only enforces
non-negativity via CHECK constraints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_app.db.base import Base, TimestampMixin, uuid7

if TYPE_CHECKING:
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer


VALID_ORDER_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "paid",
        "in_production",
        "shipped",
        "delivered",
        "refunded",
        "cancelled",
        "problem",
    }
)


class Order(Base, TimestampMixin):
    """One customer transaction (one or more line items)."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customers.id",
            ondelete="RESTRICT",
            name="fk_orders_customer_id_customers",
        ),
        nullable=False,
    )
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "addresses.id",
            ondelete="RESTRICT",
            name="fk_orders_shipping_address_id_addresses",
        ),
        nullable=True,
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
        default="pending",
    )
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    tax_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(length=3),
        nullable=False,
        server_default=text("'USD'"),
        default="USD",
    )

    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    in_production_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'web'"), default="web"
    )

    # Phase 5b: internal cost in cents (Prodigi cost on reprints, primarily).
    internal_cost_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="orders"
    )
    shipping_address: Mapped[Address | None] = relationship("Address")
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','paid','in_production','shipped',"
            "'delivered','refunded','cancelled','problem')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "subtotal_cents >= 0", name="ck_orders_subtotal_non_negative"
        ),
        CheckConstraint(
            "shipping_cents >= 0", name="ck_orders_shipping_non_negative"
        ),
        CheckConstraint("tax_cents >= 0", name="ck_orders_tax_non_negative"),
        CheckConstraint(
            "total_cents >= 0", name="ck_orders_total_non_negative"
        ),
        UniqueConstraint(
            "stripe_payment_intent_id",
            name="uq_orders_stripe_payment_intent_id",
        ),
        Index(
            "ix_orders_customer_id_created_at",
            "customer_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_orders_status_created_at",
            "status",
            text("created_at DESC"),
        ),
    )

    # ------------------------------------------------------------------
    # Validation helpers (pure — for tests and pre-commit assertions).
    # ------------------------------------------------------------------
    def total_matches_components(self) -> bool:
        """True if total_cents == subtotal + shipping + tax."""
        return self.total_cents == (
            (self.subtotal_cents or 0)
            + (self.shipping_cents or 0)
            + (self.tax_cents or 0)
        )

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} status={self.status!r} "
            f"total_cents={self.total_cents} currency={self.currency}>"
        )


class OrderItem(Base):
    """One line of an Order. Snapshots SKU display fields at order time."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "orders.id",
            ondelete="RESTRICT",
            name="fk_order_items_order_id_orders",
        ),
        nullable=False,
    )
    render_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "render_specs.id",
            ondelete="RESTRICT",
            name="fk_order_items_render_spec_id_render_specs",
        ),
        nullable=True,
    )
    prodigi_sku_internal: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "prodigi_skus.internal_sku",
            ondelete="RESTRICT",
            name="fk_order_items_prodigi_sku_internal_prodigi_skus",
        ),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finish_display: Mapped[str] = mapped_column(Text, nullable=False)
    size_inches: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    order: Mapped[Order] = relationship("Order", back_populates="items")

    __table_args__ = (
        CheckConstraint(
            "quantity > 0", name="ck_order_items_quantity_positive"
        ),
        CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_order_items_unit_price_non_negative",
        ),
        CheckConstraint(
            "line_total_cents >= 0",
            name="ck_order_items_line_total_non_negative",
        ),
        Index("ix_order_items_order_id", "order_id"),
    )

    def line_total_matches(self) -> bool:
        """True if line_total_cents == unit_price_cents * quantity."""
        return self.line_total_cents == (
            (self.unit_price_cents or 0) * (self.quantity or 0)
        )

    def __repr__(self) -> str:
        return (
            f"<OrderItem sku={self.prodigi_sku_internal!r} qty={self.quantity} "
            f"unit_cents={self.unit_price_cents}>"
        )


__all__ = [
    "VALID_ORDER_STATUSES",
    "Order",
    "OrderItem",
]
