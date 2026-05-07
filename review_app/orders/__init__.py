"""Orders module — placed orders + their line items.

* Phase 3a (parallel agent): ORM models in :mod:`review_app.orders.models`.
* Phase 3b (this work):
    - :mod:`review_app.orders.service` — pure business logic
      (``place_order_from_cart`` snapshots a cart into an Order +
      OrderItems, idempotent by stripe_payment_intent_id).
    - :mod:`review_app.orders.jobs` — RQ-callable
      ``create_prodigi_order_job(order_id)`` that the outbox drainer
      invokes after ``checkout.session.completed`` lands.

The customer-facing order pages (``/orders/<id>``, "track my order")
land in Phase 4. ``init_app`` is a no-op for now — there's no admin
blueprint yet — but reserves the wiring slot.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.orders.models import (
    VALID_ORDER_STATUSES,
    Order,
    OrderItem,
)

__all__ = [
    "VALID_ORDER_STATUSES",
    "Order",
    "OrderItem",
    "init_app",
]


def init_app(app: Flask) -> None:
    """Register order admin/lookup routes. No-op until Phase 4."""
    _ = app
    return None
