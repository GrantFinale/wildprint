"""Cmd+K global search — Phase 6 polish: real cross-entity full-text search.

Routes:
    GET /admin/search?q=<query>  — render a results page grouped by entity.

Searches across:

* orders         — by ID prefix, stripe_payment_intent_id, or status
* customers      — by email, first_name, last_name
* prodigi_skus   — by internal_sku, finish, size_inches
* species        — by slug, common_name, scientific_name (JSON file)
* prodigi_orders — by prodigi_order_id
* audit_log      — by action, target_type, target_id

On Postgres the migration 0024 maintains tsvector + GIN indexes; this
service uses the ``@@ websearch_to_tsquery('simple', :q)`` operator when
available. On SQLite (test fixtures) we fall back to ``LIKE %q%``
across the same columns. Both paths cap results at 10 per group.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import render_template, request
from flask.typing import ResponseReturnValue
from sqlalchemy import or_, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from review_app.admin._helpers import crumbs
from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role
from review_app.db import get_session

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_RESULT_LIMIT: int = 10


@dataclass(frozen=True)
class SearchHit:
    """One search result row, group-agnostic."""

    label: str
    sublabel: str
    href: str


@dataclass(frozen=True)
class SearchGroup:
    """One named group of results in the rendered page."""

    name: str
    hits: list[SearchHit]


def _is_postgres(session: Session) -> bool:
    try:
        return session.bind.dialect.name == "postgresql"  # type: ignore[union-attr]
    except Exception:
        return False


def _search_orders(session: Session, q: str) -> list[SearchHit]:
    from review_app.orders.models import Order

    qlow = q.lower()
    stmt = select(Order).limit(_RESULT_LIMIT)
    try:
        if _is_postgres(session):
            stmt = stmt.where(
                text(
                    "search_vector @@ websearch_to_tsquery('simple', :q) "
                    "OR id::text ILIKE :pat OR COALESCE(stripe_payment_intent_id, '') ILIKE :pat"
                ).bindparams(q=q, pat=f"%{qlow}%")
            )
        else:
            stmt = stmt.where(
                or_(
                    Order.stripe_payment_intent_id.ilike(f"%{qlow}%"),
                    text("CAST(id AS TEXT) LIKE :pat").bindparams(pat=f"%{qlow}%"),
                    Order.status.ilike(f"%{qlow}%"),
                )
            )
        rows = session.execute(stmt).scalars().all()
    except (OperationalError, ProgrammingError):
        return []

    return [
        SearchHit(
            label=f"Order {str(o.id)[:8]}",
            sublabel=(
                f"{o.status} — {o.stripe_payment_intent_id or '(no charge)'}"
            ),
            href=f"/admin/orders/{o.id}",
        )
        for o in rows
    ]


def _search_customers(session: Session, q: str) -> list[SearchHit]:
    from review_app.customers.models import Customer

    qlow = q.lower()
    stmt = select(Customer).limit(_RESULT_LIMIT)
    try:
        if _is_postgres(session):
            stmt = stmt.where(
                text(
                    "search_vector @@ websearch_to_tsquery('simple', :q) "
                    "OR email ILIKE :pat OR COALESCE(name, '') ILIKE :pat"
                ).bindparams(q=q, pat=f"%{qlow}%")
            )
        else:
            stmt = stmt.where(
                or_(
                    Customer.email.ilike(f"%{qlow}%"),
                    Customer.name.ilike(f"%{qlow}%"),
                )
            )
        rows = session.execute(stmt).scalars().all()
    except (OperationalError, ProgrammingError):
        return []

    return [
        SearchHit(
            label=c.email,
            sublabel=c.name or "(no name)",
            href=f"/admin/customers/{c.id}",
        )
        for c in rows
    ]


def _search_prodigi_skus(session: Session, q: str) -> list[SearchHit]:
    try:
        from review_app.prodigi.db_models import ProdigiSku
    except ImportError:
        return []

    qlow = q.lower()
    stmt = select(ProdigiSku).limit(_RESULT_LIMIT)
    try:
        if _is_postgres(session):
            stmt = stmt.where(
                text(
                    "search_vector @@ websearch_to_tsquery('simple', :q) "
                    "OR internal_sku ILIKE :pat"
                ).bindparams(q=q, pat=f"%{qlow}%")
            )
        else:
            stmt = stmt.where(
                or_(
                    ProdigiSku.internal_sku.ilike(f"%{qlow}%"),
                    ProdigiSku.finish.ilike(f"%{qlow}%"),
                    ProdigiSku.size_inches.ilike(f"%{qlow}%"),
                )
            )
        rows = session.execute(stmt).scalars().all()
    except (OperationalError, ProgrammingError):
        return []

    return [
        SearchHit(
            label=s.internal_sku,
            sublabel=f"{s.size_inches} {s.finish}",
            href=f"/admin/catalog/frame-skus?size={s.size_inches}",
        )
        for s in rows
    ]


def _search_species(q: str) -> list[SearchHit]:
    try:
        from review_app.app import load_species  # legacy loader
    except ImportError:
        return []

    qlow = q.lower()
    try:
        species = load_species()
    except Exception:
        return []

    hits: list[SearchHit] = []
    for sp in species:
        slug = (sp.get("slug") or "").lower()
        common = (sp.get("common_name") or sp.get("name") or "").lower()
        sci = (sp.get("scientific_name") or "").lower()
        if qlow in slug or qlow in common or qlow in sci:
            hits.append(
                SearchHit(
                    label=sp.get("common_name") or sp.get("name") or slug,
                    sublabel=sp.get("scientific_name") or "",
                    href=f"/species/{slug}" if slug else "/admin/catalog/species",
                )
            )
        if len(hits) >= _RESULT_LIMIT:
            break
    return hits


def _search_prodigi_orders(session: Session, q: str) -> list[SearchHit]:
    try:
        from review_app.prodigi.db_models import ProdigiOrder
    except ImportError:
        return []

    qlow = q.lower()
    stmt = select(ProdigiOrder).limit(_RESULT_LIMIT)
    try:
        stmt = stmt.where(ProdigiOrder.prodigi_order_id.ilike(f"%{qlow}%"))
        rows = session.execute(stmt).scalars().all()
    except (OperationalError, ProgrammingError):
        return []

    return [
        SearchHit(
            label=p.prodigi_order_id or "(unsubmitted)",
            sublabel=getattr(p, "status_stage", None) or "",
            href=f"/admin/fulfillment/orders/{p.id}",
        )
        for p in rows
    ]


def _search_audit_log(session: Session, q: str) -> list[SearchHit]:
    try:
        from review_app.audit.models import AuditLogEntry
    except ImportError:
        return []

    qlow = q.lower()
    stmt = select(AuditLogEntry).limit(_RESULT_LIMIT)
    try:
        if _is_postgres(session):
            stmt = stmt.where(
                text(
                    "search_vector @@ websearch_to_tsquery('simple', :q) "
                    "OR action ILIKE :pat"
                ).bindparams(q=q, pat=f"%{qlow}%")
            )
        else:
            stmt = stmt.where(
                or_(
                    AuditLogEntry.action.ilike(f"%{qlow}%"),
                    AuditLogEntry.target_type.ilike(f"%{qlow}%"),
                    AuditLogEntry.target_id.ilike(f"%{qlow}%"),
                )
            )
        rows = (
            session.execute(stmt.order_by(AuditLogEntry.created_at.desc()))
            .scalars()
            .all()
        )
    except (OperationalError, ProgrammingError):
        return []

    return [
        SearchHit(
            label=e.action,
            sublabel=f"{e.target_type or ''}/{e.target_id or ''}",
            href="/admin/settings/audit",
        )
        for e in rows
    ]


def _looks_like_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, TypeError):
        return False


@admin_bp.route("/search", methods=["GET"])
@requires_role("admin", "staff", "viewer")
def search() -> ResponseReturnValue:
    """Render the cross-entity search page."""
    q = (request.args.get("q") or "").strip()

    groups: list[SearchGroup] = []
    if q:
        try:
            with get_session() as session:
                orders = _search_orders(session, q)
                customers = _search_customers(session, q)
                skus = _search_prodigi_skus(session, q)
                prodigi_orders = _search_prodigi_orders(session, q)
                audit = _search_audit_log(session, q)
        except Exception as exc:
            logger.warning("admin search DB session failed: %s", exc)
            orders = customers = skus = prodigi_orders = audit = []

        species = _search_species(q)

        if orders:
            groups.append(SearchGroup("Orders", orders))
        if customers:
            groups.append(SearchGroup("Customers", customers))
        if skus:
            groups.append(SearchGroup("SKUs", skus))
        if species:
            groups.append(SearchGroup("Species", species))
        if prodigi_orders:
            groups.append(SearchGroup("Prodigi orders", prodigi_orders))
        if audit:
            groups.append(SearchGroup("Audit log", audit))

    return render_template(
        "admin/search.html",
        page_title="Search",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Search", None),
        ),
        q=q,
        groups=groups,
    )


__all__: list[str] = []
