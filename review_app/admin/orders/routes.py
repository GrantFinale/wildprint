"""Admin Orders routes — list, detail, refunds queue, test-order creator.

All four routes are registered on the parallel agent's ``admin_bp`` via
:func:`register`. Endpoint names follow ``admin.orders_*`` exactly so the
sidebar nav (see ``review_app.admin.nav``) resolves correctly.
"""
from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from flask import (
    Response,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
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
ORDER_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # (slug, label, statuses-included)
    ("all", "All", ()),
    ("open", "Open", ("pending", "paid")),
    ("in_production", "In production", ("in_production",)),
    ("shipped", "Shipped", ("shipped", "delivered")),
    ("refunded", "Refunded", ("refunded",)),
    ("problem", "Problem", ("problem",)),
)

PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_int(raw: str | None, default: int) -> int:
    """Best-effort int parser that always returns the default on garbage."""
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _statuses_for_tab(tab: str) -> tuple[str, ...]:
    for slug, _label, statuses in ORDER_TABS:
        if slug == tab:
            return statuses
    return ()


def _serialize_sku_summary(items: list[Any]) -> str:
    """Compact one-liner of order line items for table cells.

    e.g. "16x20 Black x1, 20x30 White x2".
    """
    parts: list[str] = []
    for item in items:
        size = getattr(item, "size_inches", "") or ""
        finish = getattr(item, "finish_display", "") or ""
        qty = getattr(item, "quantity", 1) or 1
        label = " ".join(p for p in (size, finish) if p)
        parts.append(f"{label} x{qty}".strip())
    return ", ".join(parts)


def _query_orders(
    session: Session,
    *,
    statuses: tuple[str, ...] = (),
    search: str | None = None,
    sku: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    problem_only: bool = False,
    limit: int = PAGE_SIZE,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """Return (rows, total_count) for the orders list page.

    Joins to Customer for the filters/columns; eager-loads ``items`` via
    the relationship's ``lazy='selectin'`` setting.
    """
    from review_app.customers.models import Customer
    from review_app.orders.models import Order, OrderItem

    base_filters: list[Any] = []
    if statuses:
        base_filters.append(Order.status.in_(statuses))
    if problem_only:
        base_filters.append(Order.status == "problem")
    if date_from is not None:
        base_filters.append(Order.created_at >= date_from)
    if date_to is not None:
        base_filters.append(Order.created_at <= date_to)

    join_customer = bool(search)
    join_item = bool(sku)

    stmt = select(Order)
    if join_customer and search is not None:
        stmt = stmt.join(Customer, Customer.id == Order.customer_id)
        like = f"%{search.strip().lower()}%"
        base_filters.append(
            or_(
                func.lower(Customer.email).like(like),
                func.lower(Customer.name).like(like),
            )
        )
    if join_item:
        stmt = stmt.join(OrderItem, OrderItem.order_id == Order.id)
        base_filters.append(OrderItem.prodigi_sku_internal == sku)

    if base_filters:
        stmt = stmt.where(and_(*base_filters))

    # Total count (for pagination + tab badges) — wrap in subquery so joins
    # don't multiply rows.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int(session.execute(count_stmt).scalar_one() or 0)

    stmt = (
        stmt.order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
        .distinct()
    )
    rows = list(session.execute(stmt).scalars().unique().all())
    return rows, total


def _tab_counts(session: Session) -> dict[str, int]:
    """Return ``{tab_slug: count}`` for every tab badge."""
    from review_app.orders.models import Order

    counts: dict[str, int] = {}
    for slug, _label, statuses in ORDER_TABS:
        if not statuses:
            stmt = select(func.count()).select_from(Order)
        else:
            stmt = (
                select(func.count())
                .select_from(Order)
                .where(Order.status.in_(statuses))
            )
        counts[slug] = int(session.execute(stmt).scalar_one() or 0)
    return counts


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Accept ``YYYY-MM-DD``; normalize to UTC midnight.
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Route registrar
# ---------------------------------------------------------------------------
def register(admin_bp: Blueprint) -> None:
    """Register every orders view on ``admin_bp``."""

    # -----------------------------------------------------------------
    # GET /admin/orders — list with tabs, filters, CSV export.
    # -----------------------------------------------------------------
    @admin_bp.route("/orders", methods=["GET"], endpoint="orders_list")
    @requires_role("admin", "staff", "viewer")
    def orders_list() -> Response:
        session = _admin_session.get_session()
        try:
            tab = (request.args.get("tab") or "all").strip()
            if tab not in {slug for slug, _, _ in ORDER_TABS}:
                tab = "all"
            statuses = _statuses_for_tab(tab)
            search = request.args.get("q") or None
            sku = request.args.get("sku") or None
            date_from = _parse_date(request.args.get("from"))
            date_to = _parse_date(request.args.get("to"))
            problem_only = request.args.get("problem_only") == "1"
            page = max(1, _parse_int(request.args.get("page"), 1))
            offset = (page - 1) * PAGE_SIZE

            rows, total = _query_orders(
                session,
                statuses=statuses,
                search=search,
                sku=sku,
                date_from=date_from,
                date_to=date_to,
                problem_only=problem_only,
                limit=PAGE_SIZE,
                offset=offset,
            )

            # CSV export branch — bypasses the template entirely.
            if request.args.get("format") == "csv":
                return _orders_csv_response(session, rows)

            counts = _tab_counts(session)
            customer_emails = _bulk_customer_emails(
                session, [r.customer_id for r in rows]
            )
            prodigi_ids = _bulk_prodigi_ids(session, [r.id for r in rows])

            html = render_template(
                "admin/orders/orders_list.html",
                orders=rows,
                customer_emails=customer_emails,
                prodigi_ids=prodigi_ids,
                tab=tab,
                tabs=ORDER_TABS,
                tab_counts=counts,
                total=total,
                page=page,
                page_size=PAGE_SIZE,
                page_count=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                filters={
                    "q": search or "",
                    "sku": sku or "",
                    "from": request.args.get("from") or "",
                    "to": request.args.get("to") or "",
                    "problem_only": problem_only,
                },
                sku_summary=_serialize_sku_summary,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET /admin/orders/refunds — refunds queue (admin only).
    # IMPORTANT: registered BEFORE the /orders/<order_id> route so the
    # literal "refunds" path doesn't get captured as a UUID.
    # -----------------------------------------------------------------
    @admin_bp.route("/orders/refunds", methods=["GET"], endpoint="orders_refunds")
    @requires_role("admin")
    def orders_refunds() -> Response:
        from review_app.refunds.models import Refund

        session = _admin_session.get_session()
        try:
            status = request.args.get("status") or None
            date_from = _parse_date(request.args.get("from"))
            date_to = _parse_date(request.args.get("to"))

            stmt = select(Refund)
            filters: list[Any] = []
            if status:
                filters.append(Refund.status == status)
            if date_from is not None:
                filters.append(Refund.created_at >= date_from)
            if date_to is not None:
                filters.append(Refund.created_at <= date_to)
            if filters:
                stmt = stmt.where(and_(*filters))
            stmt = stmt.order_by(Refund.created_at.desc()).limit(200)
            refunds = list(session.execute(stmt).scalars().all())

            html = render_template(
                "admin/orders/refunds_list.html",
                refunds=refunds,
                filters={
                    "status": status or "",
                    "from": request.args.get("from") or "",
                    "to": request.args.get("to") or "",
                },
                statuses=("pending", "succeeded", "failed", "cancelled"),
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET/POST /admin/orders/test — sandbox test order creator.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/orders/test", methods=["GET", "POST"], endpoint="orders_test"
    )
    @requires_role("admin", "staff")
    def orders_test() -> Response:
        from review_app.prodigi.db_models import ProdigiSku

        session = _admin_session.get_session()
        try:
            sku_rows = list(
                session.execute(
                    select(ProdigiSku)
                    .where(ProdigiSku.active.is_(True))
                    .order_by(ProdigiSku.internal_sku)
                )
                .scalars()
                .all()
            )

            if request.method == "POST":
                form = request.form
                try:
                    order_id = _create_test_order(session, form)
                except ValueError as exc:
                    return make_response(
                        render_template(
                            "admin/orders/test_order.html",
                            skus=sku_rows,
                            error=str(exc),
                            form=form,
                        ),
                        400,
                    )
                _admin_session.close_session_if_owned(session, commit=True)
                return cast(
                    "Response",
                    redirect(
                        url_for("admin.orders_detail", order_id=str(order_id))
                    ),
                )

            return make_response(
                render_template(
                    "admin/orders/test_order.html",
                    skus=sku_rows,
                    error=None,
                    form={},
                )
            )
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET /admin/orders/<order_id> — order detail.
    # Registered last so the static refunds/test paths win.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/orders/<uuid:order_id>",
        methods=["GET"],
        endpoint="orders_detail",
    )
    @requires_role("admin", "staff", "viewer")
    def orders_detail(order_id: uuid.UUID) -> Response:
        from review_app.email.outbox import OutboxEntry
        from review_app.orders.models import Order
        from review_app.prodigi.db_models import (
            ProdigiCallback,
            ProdigiOrder,
            Shipment,
        )

        session = _admin_session.get_session()
        try:
            order = session.get(Order, order_id)
            if order is None:
                return make_response(
                    render_template(
                        "admin/orders/order_detail.html",
                        order=None,
                        order_not_found=True,
                    ),
                    404,
                )

            customer = order.customer
            address = order.shipping_address
            items = list(order.items or [])

            # prodigi_orders / shipments use a dialect-portable UUID
            # column type (UUID on Postgres, TEXT on SQLite). Pass the
            # hex form on SQLite so the bind doesn't crash.
            bind = session.get_bind()
            is_sqlite = bind.dialect.name == "sqlite"
            order_key: Any = order.id.hex if is_sqlite else order.id

            prodigi_order = session.execute(
                select(ProdigiOrder).where(
                    or_(
                        ProdigiOrder.fishingposter_order_id == order_key,
                        ProdigiOrder.order_id == order_key,
                    )
                )
            ).scalar_one_or_none()

            shipments = list(
                session.execute(
                    select(Shipment).where(
                        Shipment.fishingposter_order_id == order_key
                    )
                )
                .scalars()
                .all()
            )

            # Raw event log — most-recent 20 prodigi_callbacks for the
            # corresponding prodigi_order_id (if any).
            callbacks: list[ProdigiCallback] = []
            if prodigi_order is not None and prodigi_order.prodigi_order_id:
                callbacks = list(
                    session.execute(
                        select(ProdigiCallback)
                        .where(
                            ProdigiCallback.prodigi_order_id
                            == prodigi_order.prodigi_order_id
                        )
                        .order_by(ProdigiCallback.received_at.desc())
                        .limit(20)
                    )
                    .scalars()
                    .all()
                )

            # Email log — outbox rows whose payload['to'] matches the
            # customer's email and kind starts with ``email.``. JSON-path
            # queries are dialect-specific, so do an in-Python filter.
            email_rows: list[OutboxEntry] = []
            if customer is not None and customer.email:
                outbox_stmt = (
                    select(OutboxEntry)
                    .where(OutboxEntry.kind.like("email.%"))
                    .order_by(OutboxEntry.created_at.desc())
                    .limit(100)
                )
                for row in session.execute(outbox_stmt).scalars().all():
                    payload = row.payload or {}
                    if payload.get("to") == customer.email:
                        email_rows.append(row)
                        if len(email_rows) >= 20:
                            break

            html = render_template(
                "admin/orders/order_detail.html",
                order=order,
                order_not_found=False,
                customer=customer,
                address=address,
                items=items,
                prodigi_order=prodigi_order,
                shipments=shipments,
                callbacks=callbacks,
                emails=email_rows,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)


# ---------------------------------------------------------------------------
# Detail-page bulk lookups (kept module-level so they're easy to test)
# ---------------------------------------------------------------------------
def _bulk_customer_emails(
    session: Session, customer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Email-by-customer-id lookup for the orders list table."""
    if not customer_ids:
        return {}
    from review_app.customers.models import Customer

    rows = session.execute(
        select(Customer.id, Customer.email).where(Customer.id.in_(customer_ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def _bulk_prodigi_ids(
    session: Session, order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """``ord_*`` lookup so the list view can show Prodigi IDs.

    The ``prodigi_orders.fishingposter_order_id`` and ``order_id`` columns
    are dialect-portable: native ``UUID`` on Postgres, ``TEXT`` on SQLite.
    Pass UUIDs as ``str`` (hex form) on SQLite to avoid the
    ``type 'UUID' is not supported`` bind-time crash.
    """
    if not order_ids:
        return {}
    from review_app.prodigi.db_models import ProdigiOrder

    bind = session.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    keys = [oid.hex if is_sqlite else oid for oid in order_ids]

    rows = session.execute(
        select(
            ProdigiOrder.fishingposter_order_id,
            ProdigiOrder.order_id,
            ProdigiOrder.prodigi_order_id,
        ).where(
            or_(
                ProdigiOrder.fishingposter_order_id.in_(keys),
                ProdigiOrder.order_id.in_(keys),
            )
        )
    ).all()
    out: dict[uuid.UUID, str] = {}
    # Build reverse map from string-or-UUID back to UUID for keying.
    reverse: dict[Any, uuid.UUID] = {
        (o.hex if is_sqlite else o): o for o in order_ids
    }
    for fid, oid, prodigi_id in rows:
        if not prodigi_id:
            continue
        key_raw = oid or fid
        if key_raw is None:
            continue
        key = reverse.get(key_raw)
        if key is not None:
            out[key] = prodigi_id
    return out


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def _orders_csv_response(session: Session, rows: list[Any]) -> Response:
    """Stream a CSV for the current orders list filter set."""
    customer_emails = _bulk_customer_emails(
        session, [r.customer_id for r in rows]
    )
    prodigi_ids = _bulk_prodigi_ids(session, [r.id for r in rows])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "order_id",
            "created_at",
            "status",
            "customer_email",
            "total_cents",
            "currency",
            "sku_summary",
            "prodigi_order_id",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                str(row.id),
                row.created_at.isoformat() if row.created_at else "",
                row.status,
                customer_emails.get(row.customer_id, ""),
                row.total_cents,
                row.currency,
                _serialize_sku_summary(list(row.items or [])),
                prodigi_ids.get(row.id, ""),
            ]
        )
    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        'attachment; filename="orders.csv"'
    )
    return response


# ---------------------------------------------------------------------------
# Test-order creator (the data path is documented in docs/admin-pages.md)
# ---------------------------------------------------------------------------
def _create_test_order(session: Session, form: Any) -> uuid.UUID:
    """Create a sandbox test Order without going through Stripe.

    Data path:

    1. Insert (or fetch) a synthetic customer ``test+<timestamp>@fishingposter.com``.
    2. Insert a synthetic shipping address (un-validated; sandbox-only).
    3. Insert an :class:`~review_app.orders.models.Order` with
       ``status='paid'``, ``source='admin_test'``, and a synthetic
       ``stripe_payment_intent_id`` of ``"test_pi_<uuid7-hex>"``.
    4. Insert an :class:`~review_app.orders.models.OrderItem` for the
       chosen SKU at the SKU's retail_price_cents (or 0 if unset).
    5. Enqueue an outbox row of kind ``prodigi.create_order`` with payload
       ``{"order_id": <uuid>, "test_order": True}`` so the existing worker
       routes it through the sandbox client.

    Raises ``ValueError`` for any required-field validation failure.
    """
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order, OrderItem
    from review_app.prodigi.db_models import ProdigiSku

    sku_internal = (form.get("prodigi_sku_internal") or "").strip()
    if not sku_internal:
        raise ValueError("prodigi_sku_internal is required")

    sku = session.execute(
        select(ProdigiSku).where(ProdigiSku.internal_sku == sku_internal)
    ).scalar_one_or_none()
    if sku is None:
        raise ValueError(f"Unknown SKU: {sku_internal}")

    name = (form.get("name") or "Test Customer").strip()
    line1 = (form.get("line1") or "1 Test St").strip()
    line2 = (form.get("line2") or "").strip() or None
    city = (form.get("city") or "Testville").strip()
    state = (form.get("state") or "CA").strip().upper()
    if len(state) != 2:
        raise ValueError("state must be 2 letters")
    zip_code = (form.get("zip") or "94016").strip()
    country = (form.get("country") or "US").strip().upper()
    if len(country) != 2:
        raise ValueError("country must be 2 letters")

    render_spec_raw = (form.get("render_spec_id") or "").strip()
    render_spec_id: uuid.UUID | None
    if render_spec_raw:
        try:
            render_spec_id = uuid.UUID(render_spec_raw)
        except ValueError as exc:
            raise ValueError("render_spec_id must be a UUID") from exc
    else:
        render_spec_id = None

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    customer_email = f"test+{timestamp}@fishingposter.com"

    customer = Customer.create(
        email=customer_email,
        name=name,
        marketing_opt_in=False,
        created_by_migration="admin_test_orders",
    )
    session.add(customer)
    session.flush()

    address = Address(
        customer_id=customer.id,
        name=name,
        line1=line1,
        line2=line2,
        city=city,
        state=state,
        zip=zip_code,
        country=country,
        is_default=True,
    )
    session.add(address)
    session.flush()

    unit_price = sku.retail_price_cents or 0
    order = Order(
        customer_id=customer.id,
        shipping_address_id=address.id,
        stripe_payment_intent_id=f"test_pi_{uuid.uuid4().hex}",
        status="paid",
        subtotal_cents=unit_price,
        shipping_cents=0,
        tax_cents=0,
        total_cents=unit_price,
        currency="USD",
        source="admin_test",
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    session.add(order)
    session.flush()

    item = OrderItem(
        order_id=order.id,
        render_spec_id=render_spec_id,
        prodigi_sku_internal=sku.internal_sku,
        quantity=1,
        unit_price_cents=unit_price,
        line_total_cents=unit_price,
        finish_display=sku.finish,
        size_inches=sku.size_inches,
    )
    session.add(item)
    session.flush()

    # Outbox handoff to the existing worker — kept consistent with the
    # Phase 3b checkout pipeline.
    enqueue(
        session,
        kind="prodigi.create_order",
        to=customer.email,
        payload={
            "order_id": str(order.id),
            "test_order": True,
            "force_sandbox": True,
        },
    )

    return order.id


__all__ = ["register"]


# Quiet unused-import linters during static analysis.
_ = (jsonify, flash, timedelta, os)
