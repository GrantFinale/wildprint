"""Admin topbar notifications — polled JSON endpoint (Phase 6 polish).

Replaces the Phase 4a stub on the topbar bell. The frontend polls
``GET /admin/notifications`` every 60s and updates a count badge + a
dropdown drawer with the latest items.

Polled signals (each capped at 5 items per category, 20 total):

* ``order.problem``       — orders.status='problem' (fulfillment failures)
* ``callback.error``      — prodigi_callbacks.processed_status='error'
* ``sku.low_margin``      — prodigi_skus where margin% < 60% on retail
* ``outbox.dead``         — outbox rows that have exhausted retries

The response is cached in-memory for 30s per (process, user_id) tuple
to keep the polling cheap. The cache is busted on logout via the existing
``after_request`` admin hook (best-effort — stale data for at most 30s
on one process is harmless).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Final

from flask import jsonify
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role
from review_app.db import get_session

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC: Final[int] = 30
_PER_GROUP_LIMIT: Final[int] = 5
_TOTAL_LIMIT: Final[int] = 20
_LOW_MARGIN_THRESHOLD: Final[float] = 0.60

_cache_lock = threading.Lock()
# user_id (or "" for shadow mode) -> (epoch_ts, payload_dict)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class NotificationItem:
    """One notification row in the dropdown."""

    type: str
    message: str
    link: str
    created_at: str


def reset_cache() -> None:
    """Drop every cached payload — called from tests + ``logout`` flows."""
    with _cache_lock:
        _cache.clear()


def _current_user_key() -> str:
    """Resolve a stable per-user cache key. Empty string in shadow mode."""
    try:
        from flask_login import current_user

        if not getattr(current_user, "is_authenticated", False):
            return ""
        return str(getattr(current_user, "id", ""))
    except Exception:
        return ""


def _fetch_order_problems() -> list[NotificationItem]:
    try:
        from review_app.orders.models import Order
    except ImportError:
        return []
    out: list[NotificationItem] = []
    try:
        with get_session() as session:
            rows = session.execute(
                select(Order)
                .where(Order.status == "problem")
                .order_by(Order.updated_at.desc())
                .limit(_PER_GROUP_LIMIT)
            ).scalars().all()
            for o in rows:
                out.append(
                    NotificationItem(
                        type="order.problem",
                        message=f"Order {str(o.id)[:8]} flagged 'problem'",
                        link=f"/admin/orders/{o.id}",
                        created_at=(
                            o.updated_at.isoformat() if o.updated_at else ""
                        ),
                    )
                )
    except (OperationalError, ProgrammingError):
        return []
    return out


def _fetch_callback_errors() -> list[NotificationItem]:
    try:
        from review_app.prodigi.db_models import ProdigiCallback
    except ImportError:
        return []
    out: list[NotificationItem] = []
    try:
        with get_session() as session:
            rows = session.execute(
                select(ProdigiCallback)
                .where(ProdigiCallback.processed_status == "error")
                .order_by(ProdigiCallback.received_at.desc())
                .limit(_PER_GROUP_LIMIT)
            ).scalars().all()
            for cb in rows:
                msg = (
                    f"Callback {cb.event_type} for "
                    f"{cb.prodigi_order_id or '(unsubmitted)'} failed"
                )
                out.append(
                    NotificationItem(
                        type="callback.error",
                        message=msg,
                        link="/admin/fulfillment/callbacks",
                        created_at=(
                            cb.received_at.isoformat()
                            if cb.received_at
                            else ""
                        ),
                    )
                )
    except (OperationalError, ProgrammingError):
        return []
    return out


def _fetch_low_margin_skus() -> list[NotificationItem]:
    try:
        from review_app.prodigi.db_models import ProdigiSku
    except ImportError:
        return []
    out: list[NotificationItem] = []
    try:
        with get_session() as session:
            rows = session.execute(
                select(ProdigiSku).where(ProdigiSku.active.is_(True))
            ).scalars().all()
            for sku in rows:
                retail = sku.retail_price_cents or 0
                wholesale = sku.last_quoted_wholesale_cents
                if retail <= 0 or wholesale is None:
                    continue
                margin_pct = (retail - wholesale) / retail
                if margin_pct < _LOW_MARGIN_THRESHOLD:
                    out.append(
                        NotificationItem(
                            type="sku.low_margin",
                            message=(
                                f"SKU {sku.internal_sku} margin "
                                f"{margin_pct:.0%} below 60%"
                            ),
                            link=(
                                "/admin/catalog/frame-skus?low_margin=1"
                            ),
                            created_at=(
                                sku.last_refreshed_at.isoformat()
                                if sku.last_refreshed_at
                                else ""
                            ),
                        )
                    )
                    if len(out) >= _PER_GROUP_LIMIT:
                        break
    except (OperationalError, ProgrammingError):
        return []
    return out


def _fetch_outbox_dead() -> list[NotificationItem]:
    try:
        from review_app.email.outbox import STATUS_DEAD, OutboxEntry
    except ImportError:
        return []
    out: list[NotificationItem] = []
    try:
        with get_session() as session:
            rows = session.execute(
                select(OutboxEntry)
                .where(OutboxEntry.status == STATUS_DEAD)
                .order_by(OutboxEntry.updated_at.desc())
                .limit(_PER_GROUP_LIMIT)
            ).scalars().all()
            for row in rows:
                out.append(
                    NotificationItem(
                        type="outbox.dead",
                        message=f"Outbox row #{row.id} ({row.kind}) dead-lettered",
                        link="/admin/content/email-log",
                        created_at=(
                            row.updated_at.isoformat()
                            if row.updated_at
                            else ""
                        ),
                    )
                )
    except (OperationalError, ProgrammingError):
        return []
    return out


def _build_payload() -> dict[str, Any]:
    """Aggregate all four signal sources into a single payload dict."""
    items: list[NotificationItem] = []
    items.extend(_fetch_order_problems())
    items.extend(_fetch_callback_errors())
    items.extend(_fetch_low_margin_skus())
    items.extend(_fetch_outbox_dead())
    items = items[:_TOTAL_LIMIT]
    return {
        "count": len(items),
        "items": [asdict(i) for i in items],
    }


@admin_bp.route("/notifications", methods=["GET"])
@requires_role("admin", "staff", "viewer")
def notifications() -> ResponseReturnValue:
    """Return a JSON payload of pending admin notifications."""
    key = _current_user_key()
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL_SEC:
            return jsonify(cached[1])

    payload = _build_payload()
    with _cache_lock:
        _cache[key] = (time.time(), payload)
    return jsonify(payload)


__all__ = ["notifications", "reset_cache"]
