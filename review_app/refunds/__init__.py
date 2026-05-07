"""Refunds module — Stripe refund + Prodigi cancel records.

* Phase 3a (parallel agent): :class:`Refund` ORM model.
* Phase 3b (this work): :func:`review_app.refunds.service.request_refund`
  orchestrates the Prodigi-cancel-then-Stripe-refund-then-email flow.

``init_app`` is a no-op for now; admin refund routes land in Phase 4.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.refunds.models import VALID_REFUND_STATUSES, Refund

__all__ = ["VALID_REFUND_STATUSES", "Refund", "init_app"]


def init_app(app: Flask) -> None:
    """No-op wiring stub. Implementations land in later phases."""
    return None
