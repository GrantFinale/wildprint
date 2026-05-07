"""Orders module — placed orders + their line items.

Phase 3a scaffolding: models only. Order placement, Stripe webhook handling,
and Prodigi order creation are the parallel agent's scope.
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
    """No-op wiring stub. Implementations land in later phases."""
    return None
