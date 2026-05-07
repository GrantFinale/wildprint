"""Customer-facing /account/* blueprint — magic-link auth + storefront pages.

Phase 5b. Separate from the admin auth in :mod:`review_app.auth` (which uses
Flask-Login for ``users``). Customers don't have a ``users`` row; instead we
populate ``g.current_customer`` from ``session['customer_id']`` on every
request inside this blueprint.

Public surface:
    * :data:`account_bp` — Flask blueprint mounted at ``/account``.
    * :func:`init_app` — register the blueprint on a Flask app.
    * :func:`current_customer` — Werkzeug ``LocalProxy`` for ``g.current_customer``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from flask import g
from werkzeug.local import LocalProxy

from review_app.account.routes import account_bp

if TYPE_CHECKING:
    from flask import Flask

    from review_app.customers.models import Customer


def _get_current_customer() -> Customer | None:
    return getattr(g, "current_customer", None)


# Werkzeug LocalProxy so callers can ``from review_app.account import current_customer``
# and use it like flask_login.current_user.
current_customer: Customer = LocalProxy(_get_current_customer)  # type: ignore[assignment]


def init_app(app: Flask) -> None:
    """Register :data:`account_bp` on ``app``. Idempotent."""
    if "account" not in app.blueprints:
        app.register_blueprint(account_bp)


__all__ = ["account_bp", "current_customer", "init_app"]
