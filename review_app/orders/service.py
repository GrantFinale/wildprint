"""Pure business logic for converting a cart into an Order.

Used by the Stripe webhook handler (``checkout.session.completed``) and
covered directly by unit tests.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from review_app.cart.models import Cart
from review_app.orders.models import Order, OrderItem

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class OrderServiceError(Exception):
    """Base class for order-service business-rule failures."""


class CartConversionError(OrderServiceError):
    """Raised when a cart can't be converted (empty, already converted, etc.)."""


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def place_order_from_cart(
    session: Session,
    *,
    cart: Cart,
    customer_id: uuid.UUID,
    shipping_address_id: uuid.UUID,
    stripe_payment_intent_id: str,
    shipping_cents: int = 0,
    tax_cents: int = 0,
    currency: str = "USD",
    source: str = "web",
) -> Order:
    """Snapshot a cart into a new ``orders`` + ``order_items`` row set.

    Side effects (within the caller's transaction):
      * Inserts one ``orders`` row.
      * Inserts one ``order_items`` row per cart_item, snapshotting
        ``finish_display`` + ``size_inches`` from ``prodigi_skus``.
      * Marks the cart ``status='converted'`` (so subsequent /api/cart calls
        will create a fresh empty cart).

    Idempotency: if an order already exists for ``stripe_payment_intent_id``
    (UNIQUE constraint), :class:`OrderServiceError` is NOT raised — instead
    the existing order is returned. The webhook handler depends on this so
    duplicate Stripe deliveries are no-ops.

    Caller commits.
    """
    from sqlalchemy import select

    from review_app.prodigi.db_models import ProdigiSku

    # Idempotency check.
    existing_stmt = select(Order).where(
        Order.stripe_payment_intent_id == stripe_payment_intent_id
    )
    existing = session.execute(existing_stmt).scalar_one_or_none()
    if existing is not None:
        return existing

    items = list(cart.items or [])
    if not items:
        raise CartConversionError(f"Cart {cart.id!s} has no items")

    # Pre-load the SKUs so we can snapshot finish_display + size_inches.
    sku_keys = {it.prodigi_sku_internal for it in items}
    sku_stmt = select(ProdigiSku).where(ProdigiSku.internal_sku.in_(sku_keys))
    sku_index: dict[str, ProdigiSku] = {
        s.internal_sku: s for s in session.execute(sku_stmt).scalars().all()
    }
    missing = sku_keys - sku_index.keys()
    if missing:
        raise CartConversionError(
            f"Cart references unknown SKUs: {sorted(missing)!r}"
        )

    subtotal = sum(it.unit_price_cents * it.quantity for it in items)
    total = subtotal + shipping_cents + tax_cents

    now = datetime.now(UTC)
    order = Order(
        customer_id=customer_id,
        shipping_address_id=shipping_address_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
        status="paid",
        subtotal_cents=subtotal,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
        total_cents=total,
        currency=currency,
        source=source,
        placed_at=now,
        paid_at=now,
    )
    session.add(order)
    session.flush()

    for cart_item in items:
        sku = sku_index[cart_item.prodigi_sku_internal]
        oi = OrderItem(
            order_id=order.id,
            render_spec_id=cart_item.render_spec_id,
            prodigi_sku_internal=cart_item.prodigi_sku_internal,
            quantity=cart_item.quantity,
            unit_price_cents=cart_item.unit_price_cents,
            line_total_cents=cart_item.unit_price_cents * cart_item.quantity,
            finish_display=sku.finish,
            size_inches=sku.size_inches,
        )
        session.add(oi)

    cart.status = "converted"
    session.flush()
    return order


__all__ = ["CartConversionError", "OrderServiceError", "place_order_from_cart"]
