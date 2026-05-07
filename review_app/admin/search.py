"""Cmd+K global search — Phase 4a placeholder.

Phase 5 wires real cross-entity search: orders by # / email / Prodigi id,
customers by email / name, species by common / scientific name, SKU by
internal_sku. For now, this page accepts ``?q=...`` and renders a stub
explaining the feature.

The placeholder is wired into the topbar's search form (which submits
GET to ``/admin/search``) so the cmd+K → submit flow works end-to-end
without a 404; users see a meaningful "coming soon" page instead.
"""
from __future__ import annotations

from flask import render_template, request
from flask.typing import ResponseReturnValue

from review_app.admin._helpers import crumbs
from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role


@admin_bp.route("/search", methods=["GET"])
@requires_role("admin", "staff", "viewer")
def search() -> ResponseReturnValue:
    """Render the Phase 5 placeholder for global search."""
    q = (request.args.get("q") or "").strip()
    return render_template(
        "admin/search.html",
        page_title="Search",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Search", None),
        ),
        q=q,
    )


__all__: list[str] = []
