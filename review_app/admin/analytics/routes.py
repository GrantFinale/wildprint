"""Admin Analytics routes — sales, AI usage, operations.

Endpoints registered on ``admin_bp``:

* ``admin.analytics_sales``      — ``GET /admin/analytics/sales``
* ``admin.analytics_ai_usage``   — ``GET /admin/analytics/ai-usage``
* ``admin.analytics_operations`` — ``GET /admin/analytics/operations``

All three are read-only by all authenticated roles. Charts are server-rendered
as plain HTML bar charts (no JS-charting dep) so the pages stay accessible
and degrade nicely in screen readers.
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from flask import (
    Response,
    make_response,
    render_template,
    request,
)
from sqlalchemy import and_, func, select

from review_app.admin import _session as _admin_session
from review_app.auth.decorators import requires_role

if TYPE_CHECKING:
    from flask import Blueprint
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _default_date_range() -> tuple[datetime, datetime]:
    """Last 30 days, inclusive of today, in UTC."""
    today = datetime.now(UTC)
    start = today - timedelta(days=29)
    return start.replace(hour=0, minute=0, second=0, microsecond=0), today


# ---------------------------------------------------------------------------
# Sales aggregates
# ---------------------------------------------------------------------------
def _sales_totals(
    session: Session, *, date_from: datetime, date_to: datetime
) -> dict[str, Any]:
    """Aggregate revenue, order count, AOV, refund_rate over the window."""
    from review_app.orders.models import Order

    base = (
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_cents), 0),
        )
        .where(
            and_(
                Order.created_at >= date_from,
                Order.created_at <= date_to,
                Order.status.notin_(("cancelled",)),
                Order.source != "admin_test",
            )
        )
    )
    count, revenue = session.execute(base).one()
    count = int(count or 0)
    revenue = int(revenue or 0)

    refund_count_stmt = (
        select(func.count(Order.id))
        .where(
            and_(
                Order.created_at >= date_from,
                Order.created_at <= date_to,
                Order.status == "refunded",
                Order.source != "admin_test",
            )
        )
    )
    refunded = int(session.execute(refund_count_stmt).scalar_one() or 0)

    aov = (revenue // count) if count else 0
    refund_rate = (refunded / count) if count else 0.0
    return {
        "revenue_cents": revenue,
        "order_count": count,
        "aov_cents": aov,
        "refund_count": refunded,
        "refund_rate": refund_rate,
    }


def _top_skus(
    session: Session, *, date_from: datetime, date_to: datetime
) -> list[tuple[str, int, int]]:
    """Return ``[(sku_internal, units, revenue_cents)]`` top 10."""
    from review_app.orders.models import Order, OrderItem

    stmt = (
        select(
            OrderItem.prodigi_sku_internal,
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.line_total_cents), 0),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            and_(
                Order.created_at >= date_from,
                Order.created_at <= date_to,
                Order.status.notin_(("cancelled", "refunded")),
                Order.source != "admin_test",
            )
        )
        .group_by(OrderItem.prodigi_sku_internal)
        .order_by(func.sum(OrderItem.line_total_cents).desc())
        .limit(10)
    )
    return [
        (row[0], int(row[1] or 0), int(row[2] or 0))
        for row in session.execute(stmt).all()
    ]


def _daily_revenue(
    session: Session, *, date_from: datetime, date_to: datetime
) -> list[tuple[date, int]]:
    """Per-day revenue series. SQLite-friendly (uses ``func.date``)."""
    from review_app.orders.models import Order

    stmt = (
        select(
            func.date(Order.created_at).label("d"),
            func.coalesce(func.sum(Order.total_cents), 0),
        )
        .where(
            and_(
                Order.created_at >= date_from,
                Order.created_at <= date_to,
                Order.status.notin_(("cancelled",)),
                Order.source != "admin_test",
            )
        )
        .group_by("d")
        .order_by("d")
    )
    out: list[tuple[date, int]] = []
    for d, total in session.execute(stmt).all():
        if isinstance(d, str):
            d = date.fromisoformat(d)
        out.append((d, int(total or 0)))
    return out


# ---------------------------------------------------------------------------
# AI usage aggregates
# ---------------------------------------------------------------------------
def _ai_spend(
    session: Session, *, date_from: datetime, date_to: datetime
) -> dict[str, Any]:
    """Sum cost_cents over the window + provider breakdown + top spec rows."""
    from review_app.ai.models import AIUsageLog

    base_filter = and_(
        AIUsageLog.created_at >= date_from,
        AIUsageLog.created_at <= date_to,
    )

    total = int(
        session.execute(
            select(func.coalesce(func.sum(AIUsageLog.cost_cents), 0)).where(
                base_filter
            )
        ).scalar_one()
        or 0
    )

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    today_total = int(
        session.execute(
            select(func.coalesce(func.sum(AIUsageLog.cost_cents), 0)).where(
                AIUsageLog.created_at >= today
            )
        ).scalar_one()
        or 0
    )
    yesterday_total = int(
        session.execute(
            select(func.coalesce(func.sum(AIUsageLog.cost_cents), 0)).where(
                and_(
                    AIUsageLog.created_at >= yesterday,
                    AIUsageLog.created_at < today,
                )
            )
        ).scalar_one()
        or 0
    )

    by_provider_stmt = (
        select(
            AIUsageLog.provider,
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.cost_cents), 0),
        )
        .where(base_filter)
        .group_by(AIUsageLog.provider)
        .order_by(func.sum(AIUsageLog.cost_cents).desc())
    )
    by_provider = [
        (row[0], int(row[1] or 0), int(row[2] or 0))
        for row in session.execute(by_provider_stmt).all()
    ]

    top_specs_stmt = (
        select(
            AIUsageLog.render_spec_id,
            func.coalesce(func.sum(AIUsageLog.cost_cents), 0),
        )
        .where(base_filter)
        .where(AIUsageLog.render_spec_id.is_not(None))
        .group_by(AIUsageLog.render_spec_id)
        .order_by(func.sum(AIUsageLog.cost_cents).desc())
        .limit(10)
    )
    top_specs = [
        (row[0], int(row[1] or 0))
        for row in session.execute(top_specs_stmt).all()
    ]

    daily_stmt = (
        select(
            func.date(AIUsageLog.created_at),
            AIUsageLog.provider,
            func.coalesce(func.sum(AIUsageLog.cost_cents), 0),
        )
        .where(base_filter)
        .group_by(func.date(AIUsageLog.created_at), AIUsageLog.provider)
        .order_by(func.date(AIUsageLog.created_at))
    )
    daily: dict[str, dict[str, int]] = {}
    for d, provider, cost in session.execute(daily_stmt).all():
        if isinstance(d, datetime):
            d = d.date()
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        daily.setdefault(key, {})[provider] = int(cost or 0)

    return {
        "total_cents": total,
        "today_cents": today_total,
        "yesterday_cents": yesterday_total,
        "by_provider": by_provider,
        "top_specs": top_specs,
        "daily": sorted(daily.items()),
    }


# ---------------------------------------------------------------------------
# Operations aggregates
# ---------------------------------------------------------------------------
def _operations_metrics(session: Session) -> dict[str, Any]:
    """Avg time-to-production, avg time-to-ship, error & reprint rates."""
    from review_app.orders.models import Order

    # Avg(placed_at -> shipped_at) for shipped orders.
    shipped = list(
        session.execute(
            select(Order.placed_at, Order.shipped_at).where(
                Order.shipped_at.is_not(None),
                Order.placed_at.is_not(None),
                Order.source != "admin_test",
            )
        ).all()
    )
    ttship_secs = [
        (s.shipped_at - s.placed_at).total_seconds() for s in shipped
    ]
    avg_ttship = (sum(ttship_secs) / len(ttship_secs)) if ttship_secs else 0.0

    # Phase 5b: real measurement now that orders.in_production_at exists
    # (migration 0020). Compute avg(in_production_at - paid_at) over orders
    # that have both. Fall back to the old approximation only if the column
    # is empty (e.g. fresh DB before any production transitions).
    in_prod_rows = list(
        session.execute(
            select(Order.paid_at, Order.in_production_at).where(
                Order.in_production_at.is_not(None),
                Order.paid_at.is_not(None),
                Order.source != "admin_test",
            )
        ).all()
    )
    ttprod_secs = [
        (row.in_production_at - row.paid_at).total_seconds()
        for row in in_prod_rows
    ]
    if ttprod_secs:
        avg_ttprod = sum(ttprod_secs) / len(ttprod_secs)
    elif ttship_secs:
        avg_ttprod = avg_ttship * 0.4
    else:
        avg_ttprod = 0.0

    total_orders = int(
        session.execute(
            select(func.count(Order.id)).where(Order.source != "admin_test")
        ).scalar_one()
        or 0
    )
    error_count = int(
        session.execute(
            select(func.count(Order.id)).where(
                Order.status == "problem",
                Order.source != "admin_test",
            )
        ).scalar_one()
        or 0
    )
    reprint_count = int(
        session.execute(
            select(func.count(Order.id)).where(Order.source == "reprint")
        ).scalar_one()
        or 0
    )
    error_rate = (error_count / total_orders) if total_orders else 0.0
    reprint_rate = (reprint_count / total_orders) if total_orders else 0.0

    return {
        "avg_time_to_production_seconds": avg_ttprod,
        "avg_time_to_ship_seconds": avg_ttship,
        "error_rate": error_rate,
        "reprint_rate": reprint_rate,
        "shipped_sample_size": len(ttship_secs),
        "total_orders": total_orders,
    }


# ---------------------------------------------------------------------------
# Route registrar
# ---------------------------------------------------------------------------
def register(admin_bp: Blueprint) -> None:
    """Register all Analytics views on ``admin_bp``."""

    @admin_bp.route("/analytics/sales", endpoint="analytics_sales")
    @requires_role("admin", "staff", "viewer")
    def analytics_sales() -> Response:
        session = _admin_session.get_session()
        try:
            df = _parse_date(request.args.get("from"))
            dt = _parse_date(request.args.get("to"))
            if df is None or dt is None:
                df, dt = _default_date_range()

            totals = _sales_totals(session, date_from=df, date_to=dt)
            top = _top_skus(session, date_from=df, date_to=dt)
            daily = _daily_revenue(session, date_from=df, date_to=dt)

            if request.args.get("format") == "csv":
                return _sales_csv(daily, top, totals)

            html = render_template(
                "admin/analytics/sales.html",
                totals=totals,
                top_skus=top,
                daily=daily,
                date_from=df,
                date_to=dt,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    @admin_bp.route("/analytics/ai-usage", endpoint="analytics_ai_usage")
    @requires_role("admin", "staff", "viewer")
    def analytics_ai_usage() -> Response:
        session = _admin_session.get_session()
        try:
            df = _parse_date(request.args.get("from"))
            dt = _parse_date(request.args.get("to"))
            if df is None or dt is None:
                df, dt = _default_date_range()
            provider_filter = request.args.get("provider") or None
            if provider_filter:
                # Filter is only used for the per-provider table below;
                # the daily/by-provider aggregates always show the full
                # multi-provider view.
                pass

            data = _ai_spend(session, date_from=df, date_to=dt)
            html = render_template(
                "admin/analytics/ai_usage.html",
                **data,
                date_from=df,
                date_to=dt,
                provider_filter=provider_filter or "",
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    @admin_bp.route("/analytics/operations", endpoint="analytics_operations")
    @requires_role("admin", "staff", "viewer")
    def analytics_operations() -> Response:
        session = _admin_session.get_session()
        try:
            metrics = _operations_metrics(session)
            html = render_template(
                "admin/analytics/operations.html",
                metrics=metrics,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)


# ---------------------------------------------------------------------------
# CSV export for Sales
# ---------------------------------------------------------------------------
def _sales_csv(
    daily: list[tuple[date, int]],
    top: list[tuple[str, int, int]],
    totals: dict[str, Any],
) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "value"])
    writer.writerow(["totals", "revenue_cents", totals["revenue_cents"]])
    writer.writerow(["totals", "order_count", totals["order_count"]])
    writer.writerow(["totals", "aov_cents", totals["aov_cents"]])
    writer.writerow(["totals", "refund_rate", f"{totals['refund_rate']:.4f}"])
    for d, cents in daily:
        writer.writerow(["daily", d.isoformat(), cents])
    for sku, units, revenue in top:
        writer.writerow(["sku", sku, f"{units}|{revenue}"])
    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        'attachment; filename="sales.csv"'
    )
    return response


__all__ = ["register"]
