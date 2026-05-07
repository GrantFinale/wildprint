"""Cart module — multi-item shopping cart, anonymous and customer-bound.

Phase 3a (parallel agent): models live in :mod:`review_app.cart.models`.
Phase 3b (this work): :mod:`review_app.cart.routes` + :mod:`review_app.cart.service`
deliver the HTTP surface (``/api/cart/*`` JSON endpoints + the ``/cart`` page)
and the pure-Python business logic (``add_item``, ``update_quantity``,
``compute_totals``, ``merge_anonymous_into_customer``).

Public API
----------
- ``cart_bp`` — Flask Blueprint registered by :func:`init_app`.
- ``init_app(app)`` — registers the blueprint on a Flask app. Idempotent.
- :class:`Cart`, :class:`CartItem` — re-exported from
  :mod:`review_app.cart.models` for callers that want the SQLA models.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.cart.models import Cart, CartItem
from review_app.cart.routes import cart_bp

__all__ = ["Cart", "CartItem", "cart_bp", "init_app"]


_BLUEPRINT_REGISTERED_FLAG = "_wildprint_cart_bp_registered"


def init_app(app: Flask) -> None:
    """Register the cart blueprint on a Flask app (idempotent).

    Reads no env vars and performs no I/O — only blueprint registration. Safe
    to call from inside the legacy ``review_app.app`` monolith's bottom-of-file
    wiring without re-ordering imports.
    """
    if app.config.get(_BLUEPRINT_REGISTERED_FLAG):
        return
    app.register_blueprint(cart_bp)
    app.config[_BLUEPRINT_REGISTERED_FLAG] = True
