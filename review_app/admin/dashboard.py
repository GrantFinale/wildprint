"""Dashboard data helpers — stat cards + top SKU table queries.

Real DB queries against the existing tables (orders, ai_usage, prodigi_*).
Each helper returns a typed dict so the template doesn't have to know
about SQLAlchemy. All queries are date-bounded; the view passes the
selected range (today / 7d / 30d / mtd).

When the underlying tables are missing or empty (fresh staging), the
helpers return zeroed structs rather than raising. This lets the
Dashboard render cleanly on day one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Range tokens accepted on the Dashboard query string.
RangeToken = Literal["today", "7d", "30d", "mtd"]
VALID_RANGES: frozenset[str] = frozenset({"today", "7d", "30d", "mtd"})


@dataclass(frozen=True)
class StatCards:
    """Top-of-page numeric KPIs."""

    todays_orders: int
    todays_revenue_cents: int
    in_production: int
    shipped_7d: int
    error_count: int
    ai_spend_mtd_cents: int

    def todays_revenue_dollars(self) -> str:
        return f"${self.todays_revenue_cents / 100:,.2f}"

    def ai_spend_mtd_dollars(self) -> str:
        return f"${self.ai_spend_mtd_cents / 100:,.2f}"


@dataclass(frozen=True)
class TopSkuRow:
    """One row in the "top SKU last 7 days" table."""

    internal_sku: str
    units: int
    revenue_cents: int

    def revenue_dollars(self) -> str:
        return f"${self.revenue_cents / 100:,.2f}"


def _range_window(token: str) -> tuple[datetime, datetime]:
    """Resolve a range token to ``(start, end)`` UTC datetimes.

    ``end`` is always ``now``; ``start`` is the lower bound. Unknown
    tokens fall back to ``today``.
    """
    now = datetime.now(UTC)
    if token == "7d":
        return now - timedelta(days=7), now
    if token == "30d":
        return now - timedelta(days=30), now
    if token == "mtd":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    # default: today
    return now.replace(hour=0, minute=0, second=0, microsecond=0), now


def _safe_scalar(session: Session, stmt: object, default: int = 0) -> int:
    """Run a scalar query; return ``default`` if the table doesn't exist yet.

    Phase 4a runs in environments where some 3a/3b tables exist and others
    don't — we don't want a missing ``orders`` table on a fresh dev DB to
    crash the dashboard.
    """
    try:
        result = session.execute(stmt).scalar()  # type: ignore[call-overload]
    except (OperationalError, ProgrammingError) as exc:
        logger.debug("dashboard query skipped (table missing?): %s", exc)
        return default
    return int(result or default)


def stat_cards(session: Session) -> StatCards:
    """Compute the six top-row KPIs. Always returns — never raises."""
    today_start, _ = _range_window("today")
    week_start, _ = _range_window("7d")
    mtd_start, _ = _range_window("mtd")

    # Orders + revenue today. Guard each query so a missing 3a/3b table
    # doesn't poison the whole dashboard.
    try:
        from review_app.orders.models import Order

        todays_orders = _safe_scalar(
            session,
            select(func.count(Order.id)).where(Order.created_at >= today_start),
        )
        todays_revenue = _safe_scalar(
            session,
            select(func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.created_at >= today_start
            ),
        )
        in_production = _safe_scalar(
            session,
            select(func.count(Order.id)).where(Order.status == "in_production"),
        )
        shipped_7d = _safe_scalar(
            session,
            select(func.count(Order.id)).where(
                Order.status == "shipped",
                Order.updated_at >= week_start,
            ),
        )
    except ImportError:
        todays_orders = todays_revenue = in_production = shipped_7d = 0

    # Prodigi error count — orders in 'rejected' or 'on_hold' status.
    try:
        from review_app.prodigi.db_models import ProdigiOrder

        error_count = _safe_scalar(
            session,
            select(func.count(ProdigiOrder.id)).where(
                ProdigiOrder.status_stage.in_(["Rejected", "OnHold"])
            ),
        )
    except ImportError:
        error_count = 0

    # AI spend MTD.
    try:
        from review_app.ai.models import AIUsageLog

        ai_spend = _safe_scalar(
            session,
            select(func.coalesce(func.sum(AIUsageLog.cost_cents), 0)).where(
                AIUsageLog.created_at >= mtd_start
            ),
        )
    except ImportError:
        ai_spend = 0

    return StatCards(
        todays_orders=todays_orders,
        todays_revenue_cents=todays_revenue,
        in_production=in_production,
        shipped_7d=shipped_7d,
        error_count=error_count,
        ai_spend_mtd_cents=ai_spend,
    )


def top_skus_7d(session: Session, limit: int = 10) -> list[TopSkuRow]:
    """Return the top SKUs by units sold over the last 7 days.

    Returns an empty list when the order tables aren't populated yet.
    """
    week_start, _ = _range_window("7d")
    try:
        from review_app.orders.models import Order, OrderItem

        stmt = (
            select(
                OrderItem.prodigi_sku_internal.label("internal_sku"),
                func.sum(OrderItem.quantity).label("units"),
                func.sum(OrderItem.line_total_cents).label("revenue_cents"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.created_at >= week_start)
            .group_by(OrderItem.prodigi_sku_internal)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(limit)
        )
    except ImportError:
        return []

    try:
        rows = session.execute(stmt).all()
    except (OperationalError, ProgrammingError) as exc:
        logger.debug("top_skus query skipped (table missing?): %s", exc)
        return []

    return [
        TopSkuRow(
            internal_sku=str(r.internal_sku),
            units=int(r.units or 0),
            revenue_cents=int(r.revenue_cents or 0),
        )
        for r in rows
    ]


__all__ = [
    "VALID_RANGES",
    "RangeToken",
    "StatCards",
    "TopSkuRow",
    "stat_cards",
    "top_skus_7d",
]
