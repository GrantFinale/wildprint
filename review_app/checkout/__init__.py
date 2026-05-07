"""Checkout module — Stripe Checkout for the physical-product flow.

Phase 3b. Two surfaces:

* :mod:`review_app.checkout.routes` — `POST /api/checkout/start` (NEW route;
  the legacy `POST /api/create-checkout-session` for the $49 unlock keeps
  working unchanged in `review_app.app`).
* :mod:`review_app.checkout.webhook` — `POST /webhook/stripe/v2`, which
  handles physical-order events with idempotent dedup against the
  ``stripe_events`` table.
* :mod:`review_app.checkout.stripe_client` — thin typed wrapper around the
  ``stripe`` SDK so tests can monkey-patch a small surface.

Public API
----------
- ``checkout_bp`` — Blueprint registered by :func:`init_app`.
- ``init_app(app)`` — register the blueprint(s).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.checkout.routes import checkout_bp
from review_app.checkout.webhook import webhook_bp

__all__ = ["checkout_bp", "init_app", "webhook_bp"]


_FLAG_CHECKOUT = "_wildprint_checkout_bp_registered"
_FLAG_WEBHOOK = "_wildprint_stripe_webhook_v2_registered"


def init_app(app: Flask) -> None:
    """Register the checkout + Stripe-webhook-v2 blueprints (idempotent)."""
    if not app.config.get(_FLAG_CHECKOUT):
        app.register_blueprint(checkout_bp)
        app.config[_FLAG_CHECKOUT] = True
    if not app.config.get(_FLAG_WEBHOOK):
        app.register_blueprint(webhook_bp)
        app.config[_FLAG_WEBHOOK] = True
