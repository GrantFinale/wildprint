"""Admin Customers routes — all-customers list + per-customer detail.

Endpoints registered on ``admin_bp``:

* ``admin.customers_list``   — ``GET /admin/customers``
* ``admin.customers_detail`` — ``GET /admin/customers/<customer_id>``
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flask import (
    Response,
    flash,
    make_response,
    render_template,
    request,
)
from sqlalchemy import and_, func, or_, select

from review_app.admin import _session as _admin_session
from review_app.auth.decorators import requires_role

if TYPE_CHECKING:
    from flask import Blueprint
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LTV_BUCKETS: tuple[tuple[str, str, int], ...] = (
    # (slug, label, min-cents)
    ("any", "Any value", 0),
    ("over_0", "> $0", 1),
    ("over_200", "> $200", 20_000),
    ("over_500", "> $500", 50_000),
)

PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# LTV computation — pure helpers
# ---------------------------------------------------------------------------
def _ltv_by_customer(
    session: Session, customer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, Any]]:
    """Return ``{customer_id: (order_count, ltv_cents, last_order_at)}``.

    Sums ``orders.total_cents`` for non-refunded, non-cancelled orders only.
    """
    if not customer_ids:
        return {}
    from review_app.orders.models import Order

    stmt = (
        select(
            Order.customer_id,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_cents), 0),
            func.max(Order.created_at),
        )
        .where(Order.customer_id.in_(customer_ids))
        .where(Order.status.notin_(("refunded", "cancelled")))
        .group_by(Order.customer_id)
    )
    out: dict[uuid.UUID, tuple[int, int, Any]] = {}
    for cid, count, total, last_at in session.execute(stmt).all():
        out[cid] = (int(count or 0), int(total or 0), last_at)
    return out


def _problem_flag_by_customer(
    session: Session, customer_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Return the subset of customer_ids that have any unresolved problem order."""
    if not customer_ids:
        return set()
    from review_app.orders.models import Order

    stmt = (
        select(Order.customer_id)
        .where(Order.customer_id.in_(customer_ids))
        .where(Order.status == "problem")
        .distinct()
    )
    return {row[0] for row in session.execute(stmt).all()}


# ---------------------------------------------------------------------------
# Route registrar
# ---------------------------------------------------------------------------
def register(admin_bp: Blueprint) -> None:
    """Register all Customers views on ``admin_bp``."""

    @admin_bp.route("/customers", methods=["GET"], endpoint="customers_list")
    @requires_role("admin", "staff", "viewer")
    def customers_list() -> Response:
        from review_app.customers.models import Customer

        session = _admin_session.get_session()
        try:
            search = (request.args.get("q") or "").strip().lower()
            bucket_slug = request.args.get("bucket") or "any"
            min_cents = next(
                (m for slug, _label, m in LTV_BUCKETS if slug == bucket_slug),
                0,
            )
            page = max(1, int(request.args.get("page") or 1))
            offset = (page - 1) * PAGE_SIZE

            stmt = select(Customer).where(Customer.deleted_at.is_(None))
            if search:
                like = f"%{search}%"
                stmt = stmt.where(
                    or_(
                        func.lower(Customer.email).like(like),
                        func.lower(Customer.name).like(like),
                    )
                )

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(session.execute(count_stmt).scalar_one() or 0)

            customers = list(
                session.execute(
                    stmt.order_by(Customer.created_at.desc())
                    .limit(PAGE_SIZE)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            customer_ids = [c.id for c in customers]
            ltv = _ltv_by_customer(session, customer_ids)
            problems = _problem_flag_by_customer(session, customer_ids)

            # Apply LTV bucket filter post-query (cheap because LTVs are
            # already aggregated per page).
            if min_cents > 0:
                customers = [
                    c for c in customers if ltv.get(c.id, (0, 0, None))[1] >= min_cents
                ]

            html = render_template(
                "admin/customers/customers_list.html",
                customers=customers,
                ltv=ltv,
                problems=problems,
                buckets=LTV_BUCKETS,
                filters={"q": search, "bucket": bucket_slug},
                page=page,
                page_size=PAGE_SIZE,
                total=total,
                page_count=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    @admin_bp.route(
        "/customers/<uuid:customer_id>",
        methods=["GET"],
        endpoint="customers_detail",
    )
    @requires_role("admin", "staff", "viewer")
    def customers_detail(customer_id: uuid.UUID) -> Response:
        from review_app.customers.models import Customer
        from review_app.email.outbox import OutboxEntry
        from review_app.orders.models import Order

        session = _admin_session.get_session()
        try:
            customer = session.get(Customer, customer_id)
            if customer is None:
                return make_response(
                    render_template(
                        "admin/customers/customer_detail.html",
                        customer=None,
                        not_found=True,
                    ),
                    404,
                )

            addresses = list(customer.addresses or [])
            orders = list(
                session.execute(
                    select(Order)
                    .where(Order.customer_id == customer.id)
                    .order_by(Order.created_at.desc())
                    .limit(50)
                )
                .scalars()
                .all()
            )

            # Email log — manual filter on payload['to'] (JSON-portable).
            email_rows: list[OutboxEntry] = []
            outbox_stmt = (
                select(OutboxEntry)
                .where(OutboxEntry.kind.like("email.%"))
                .order_by(OutboxEntry.created_at.desc())
                .limit(200)
            )
            for row in session.execute(outbox_stmt).scalars().all():
                if (row.payload or {}).get("to") == customer.email:
                    email_rows.append(row)
                    if len(email_rows) >= 30:
                        break

            ltv_map = _ltv_by_customer(session, [customer.id])
            order_count, ltv_cents, last_at = ltv_map.get(customer.id, (0, 0, None))

            html = render_template(
                "admin/customers/customer_detail.html",
                customer=customer,
                addresses=addresses,
                orders=orders,
                emails=email_rows,
                order_count=order_count,
                ltv_cents=ltv_cents,
                last_order_at=last_at,
                not_found=False,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)


# Quiet linter
_ = (and_, flash)


__all__ = ["register"]
