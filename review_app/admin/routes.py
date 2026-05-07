"""Top-level admin blueprint — Dashboard + Catalog + Settings + Search.

Layout:

* This module declares :data:`admin_bp` (mounted at ``/admin``) and the
  Dashboard route directly.
* Catalog and Settings live in dedicated sub-modules
  (:mod:`review_app.admin.catalog`, :mod:`review_app.admin.settings`)
  and register their routes on :data:`admin_bp` via ``add_url_rule``.
* Stub routes for Orders / Customers / Fulfillment / Content / Analytics
  exist so the sidebar links resolve while the parallel agent fills them
  in. Each stub renders a "coming in this branch" placeholder; the parallel
  agent overwrites these with real implementations.

The module's import-time side-effect chain (registering sub-routes) keeps
``init_app`` in :mod:`review_app.admin` a one-liner.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, render_template, request
from flask.typing import ResponseReturnValue

from review_app.admin._helpers import crumbs, current_role
from review_app.admin.context import shell_context
from review_app.admin.dashboard import VALID_RANGES, stat_cards, top_skus_7d
from review_app.admin.nav import (
    visible_categories,
)
from review_app.auth.decorators import requires_role
from review_app.db import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
admin_bp: Blueprint = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="../templates/admin",
)

# Wire the per-request shell context (Prodigi env pill, notifications, build
# id) onto every admin template render. Done as a context_processor so we
# don't have to thread these into every render_template() call.
admin_bp.context_processor(shell_context)


@admin_bp.context_processor
def _inject_nav() -> dict[str, Any]:
    """Inject the role-filtered sidebar tree into every admin template."""
    role = current_role()
    return {
        "current_role": role,
        "nav_categories": visible_categories(role),
    }


# ---------------------------------------------------------------------------
# Dashboard — the /admin landing page.
# ---------------------------------------------------------------------------
@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@requires_role("admin", "staff", "viewer")
def dashboard() -> ResponseReturnValue:
    """Dashboard — six stat cards + top SKU table + range selector."""
    range_token = request.args.get("range", "today")
    if range_token not in VALID_RANGES:
        range_token = "today"

    with get_session() as session:
        cards = stat_cards(session)
        top_skus = top_skus_7d(session)

    return render_template(
        "admin/dashboard.html",
        page_title="Dashboard",
        breadcrumbs=crumbs(("Admin", "/admin"), ("Dashboard", None)),
        cards=cards,
        top_skus=top_skus,
        range_token=range_token,
    )


# ---------------------------------------------------------------------------
# Stub endpoints for sidebar items owned by the parallel agent. We register
# each stub with the EXPECTED endpoint name, then attempt to invoke the
# parallel agent's `register(admin_bp)` helpers; theirs OVERWRITE these
# stubs via Flask's view_function dict (since each parallel `register()`
# call uses `add_url_rule` with the same endpoint, last-write-wins).
#
# When a parallel sub-module is missing (Phase 4a being built without 4b
# merged yet), the stubs render a "coming in this branch" placeholder so
# the sidebar links resolve to a real page rather than a 404.
# ---------------------------------------------------------------------------
def _stub_view(label: str, category: str) -> ResponseReturnValue:
    return render_template(
        "admin/_stub.html",
        page_title=label,
        breadcrumbs=crumbs(
            ("Admin", "/admin"), (category, None), (label, None)
        ),
        label=label,
        category=category,
    )


def _register_stub(
    rule: str, endpoint: str, label: str, category: str, *roles: str
) -> None:
    """Register a stub view at ``rule`` with the given ``endpoint``.

    The view is gated by :func:`requires_role` so role-based hiding /
    403-on-direct-URL behavior matches what the real handler will enforce
    once the parallel agent's implementation lands.
    """

    @requires_role(*roles)
    def _view(label: str = label, category: str = category) -> ResponseReturnValue:
        return _stub_view(label, category)

    _view.__name__ = endpoint
    admin_bp.add_url_rule(rule, endpoint=endpoint, view_func=_view, methods=["GET"])


# Sidebar endpoints the parallel agent will own. We register a stub for each
# one BEFORE attempting to import the parallel sub-module: if their import
# fails (sub-module missing on this branch), the stub stands in; if their
# import succeeds, their `register(admin_bp)` adds the real route at the
# real URL path while the stub remains at a separate "/...-stub" path so
# Flask's no-duplicate-endpoint rule isn't violated.
#
# Stubs use a "-stub" suffix so the real URLs (e.g. /admin/orders) belong
# to the parallel agent. The sidebar resolves endpoints by NAME, not by
# URL, so it doesn't matter that the stub URL differs from the eventual
# real one.
_STUB_REGISTRATIONS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    # (rule, endpoint, label, category, roles)
    ("/customers", "customers_list", "All customers", "Customers",
     ("admin", "staff", "viewer")),
    ("/fulfillment/connection", "fulfillment_connection", "Connection",
     "Fulfillment", ("admin",)),
    ("/fulfillment/webhooks", "fulfillment_webhooks", "Webhook log",
     "Fulfillment", ("admin", "staff", "viewer")),
    ("/fulfillment/errors", "fulfillment_errors", "Error queue",
     "Fulfillment", ("admin", "staff")),
    ("/fulfillment/reprints", "fulfillment_reprints", "Reprints",
     "Fulfillment", ("admin",)),
    ("/content/email-templates", "content_email_templates",
     "Email templates", "Content", ("admin",)),
    ("/content/email-log", "content_email_log", "Email send log",
     "Content", ("admin", "staff", "viewer")),
    ("/content/marketing", "content_marketing", "Marketing pages",
     "Content", ("admin",)),
    ("/analytics/sales", "analytics_sales", "Sales", "Analytics",
     ("admin", "staff", "viewer")),
    ("/analytics/ai-usage", "analytics_ai_usage", "AI usage", "Analytics",
     ("admin", "staff", "viewer")),
    ("/analytics/operations", "analytics_operations", "Operations",
     "Analytics", ("admin", "staff", "viewer")),
)


# ---------------------------------------------------------------------------
# Try to import each Phase 4b sub-module; on success, its `register(admin_bp)`
# adds the real routes. On failure (sub-module missing), we add a stub for
# every endpoint that sub-module would have provided.
# ---------------------------------------------------------------------------
def _try_register_phase4b() -> set[str]:
    """Best-effort load of each Phase 4b sub-module.

    Returns the set of endpoint names that the parallel agent successfully
    registered. The caller registers stubs for everything else.
    """
    registered: set[str] = set()
    sub_modules = (
        ("review_app.admin.orders",
         ("orders_list", "orders_detail", "orders_refunds", "orders_test")),
        ("review_app.admin.fulfillment",
         ("fulfillment_connection", "fulfillment_webhooks",
          "fulfillment_errors", "fulfillment_reprints")),
        ("review_app.admin.customers",
         ("customers_list", "customers_detail")),
        ("review_app.admin.content",
         ("content_email_templates", "content_email_log", "content_marketing")),
        ("review_app.admin.analytics",
         ("analytics_sales", "analytics_ai_usage", "analytics_operations")),
    )
    for module_path, endpoints in sub_modules:
        try:
            mod = __import__(module_path, fromlist=["register"])
            register_fn = getattr(mod, "register", None)
            if register_fn is None:
                continue
            register_fn(admin_bp)
            registered.update(endpoints)
        except Exception as exc:
            logger.info(
                "Phase 4b sub-module %s not loaded: %s — stubs will fill in.",
                module_path, exc,
            )
    return registered


_phase4b_registered = _try_register_phase4b()

# Register stubs for any endpoint the parallel agent didn't provide.
for _rule, _endpoint, _label, _category, _roles in _STUB_REGISTRATIONS:
    if _endpoint in _phase4b_registered:
        continue
    _register_stub(_rule, _endpoint, _label, _category, *_roles)


# ---------------------------------------------------------------------------
# Sub-blueprint registration via side-effect imports.
# Catalog and Settings call ``admin_bp.add_url_rule(...)`` at import time.
# Search registers /admin/search.
# ---------------------------------------------------------------------------
from review_app.admin import (  # noqa: E402
    catalog as _catalog,  # noqa: F401
    search as _search,  # noqa: F401
    settings as _settings,  # noqa: F401
)

__all__ = ["admin_bp"]
