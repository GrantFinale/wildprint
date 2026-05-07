"""Cart + CartItem ORM models.

Mirrors ``alembic/versions/0010_carts.py``.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_app.db.base import Base, TimestampMixin, uuid7

if TYPE_CHECKING:
    from review_app.customers.models import Customer


VALID_CART_STATUSES: frozenset[str] = frozenset(
    {"open", "abandoned", "converted"}
)


class Cart(Base, TimestampMixin):
    """A shopping cart — anonymous (session_token) or customer-bound."""

    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customers.id",
            ondelete="SET NULL",
            name="fk_carts_customer_id_customers",
        ),
        nullable=True,
    )
    session_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'"), default="open"
    )

    customer: Mapped["Customer | None"] = relationship(
        "Customer", back_populates="carts"
    )
    items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="cart",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'abandoned', 'converted')",
            name="ck_carts_status",
        ),
        Index("ix_carts_customer_id", "customer_id"),
        Index("ix_carts_session_token", "session_token"),
    )

    @property
    def subtotal_cents(self) -> int:
        """Sum of line totals across all items. Pure ORM convenience."""
        return sum(
            (item.unit_price_cents or 0) * (item.quantity or 0)
            for item in (self.items or [])
        )

    def __repr__(self) -> str:
        return f"<Cart id={self.id} status={self.status!r} items={len(self.items or [])}>"


class CartItem(Base, TimestampMixin):
    """One line in a Cart: SKU + quantity + price snapshot."""

    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid7
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "carts.id",
            ondelete="CASCADE",
            name="fk_cart_items_cart_id_carts",
        ),
        nullable=False,
    )
    render_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "render_specs.id",
            ondelete="SET NULL",
            name="fk_cart_items_render_spec_id_render_specs",
        ),
        nullable=True,
    )
    prodigi_sku_internal: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "prodigi_skus.internal_sku",
            ondelete="RESTRICT",
            name="fk_cart_items_prodigi_sku_internal_prodigi_skus",
        ),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    cart: Mapped["Cart"] = relationship("Cart", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_cart_items_unit_price_non_negative",
        ),
        Index("ix_cart_items_cart_id", "cart_id"),
    )

    @property
    def line_total_cents(self) -> int:
        return (self.unit_price_cents or 0) * (self.quantity or 0)

    def __repr__(self) -> str:
        return (
            f"<CartItem sku={self.prodigi_sku_internal!r} qty={self.quantity} "
            f"unit_cents={self.unit_price_cents}>"
        )


__all__ = ["VALID_CART_STATUSES", "Cart", "CartItem"]
