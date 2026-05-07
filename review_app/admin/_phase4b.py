"""Phase 4b registration helper.

The Phase 4a parallel agent owns the top-level :data:`admin_bp` blueprint
declared in :mod:`review_app.admin` (mounted at ``/admin``). Phase 4b adds
five sub-areas (Orders / Fulfillment / Customers / Content / Analytics) but
must NOT touch the parallel agent's ``__init__.py`` or ``routes.py``.

This module provides :func:`register_phase4b_routes` — a single side-effect
function that the parallel agent's ``routes.py`` will call once with the
``admin_bp`` blueprint as its argument. Each Phase 4b sub-module exposes a
``register(admin_bp)`` function; this helper wires them all up in one place.

Usage from the parallel agent's ``review_app/admin/routes.py``::

    from flask import Blueprint
    admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

    # ... parallel agent's dashboard / catalog / settings routes here ...

    from review_app.admin._phase4b import register_phase4b_routes
    register_phase4b_routes(admin_bp)

The registration function is idempotent — calling it twice is a no-op (it
checks for a sentinel attribute on ``admin_bp``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Blueprint

_FLAG_ATTR = "_phase4b_registered"


def register_phase4b_routes(admin_bp: Blueprint) -> None:
    """Register every Phase 4b view function on the supplied ``admin_bp``.

    This call is idempotent — repeated calls are no-ops thanks to a sentinel
    attribute set on the blueprint after the first call.
    """
    if getattr(admin_bp, _FLAG_ATTR, False):
        return

    # Lazy imports — keep this module cheap to import (parallel agent's
    # ``__init__.py`` may import us before the sub-modules are stable).
    from review_app.admin.analytics import register as register_analytics
    from review_app.admin.content import register as register_content
    from review_app.admin.customers import register as register_customers
    from review_app.admin.fulfillment import register as register_fulfillment
    from review_app.admin.orders import register as register_orders

    register_orders(admin_bp)
    register_fulfillment(admin_bp)
    register_customers(admin_bp)
    register_content(admin_bp)
    register_analytics(admin_bp)

    setattr(admin_bp, _FLAG_ATTR, True)


__all__ = ["register_phase4b_routes"]
