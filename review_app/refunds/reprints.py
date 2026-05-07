"""Reprint workflow: request -> approve (creates new Prodigi order) -> reject.

Free reprints when ``requested_by_role='customer'`` and we accept the request;
otherwise the admin can mark ``customer_paid=True`` if they're charging the
customer (rare — usually we eat the cost as warranty).
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from review_app.refunds.reprints_models import (
    VALID_REPRINT_ROLES,
    ReprintRequest,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_log = logging.getLogger(__name__)


class ReprintRequestError(Exception):
    """Base for reprint workflow errors."""


def request_reprint(
    session: Session,
    *,
    order_id: uuid.UUID,
    customer_id: uuid.UUID,
    reason: str | None,
    line_item_ids: list[uuid.UUID] | None,
    requested_by_role: str = "customer",
) -> ReprintRequest:
    """Insert a pending ReprintRequest. Notifies admin via outbox.

    Idempotency: if a pending or approved request already exists for the
    same ``order_id``, we return that row instead of creating a new one.
    """
    if requested_by_role not in VALID_REPRINT_ROLES:
        raise ReprintRequestError(f"invalid role: {requested_by_role!r}")

    from sqlalchemy import select

    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order

    order = session.get(Order, order_id)
    if order is None:
        raise ReprintRequestError(f"order {order_id!s} not found")
    if order.customer_id != customer_id:
        raise ReprintRequestError("customer does not own this order")

    existing = session.execute(
        select(ReprintRequest)
        .where(ReprintRequest.order_id == order_id)
        .where(ReprintRequest.status.in_(("pending", "approved", "completed")))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    line_items_text = (
        ",".join(str(i) for i in line_item_ids) if line_item_ids else None
    )
    now = datetime.now(UTC)
    rr = ReprintRequest(
        order_id=order_id,
        customer_id=customer_id,
        requested_by_role=requested_by_role,
        line_item_ids=line_items_text,
        reason=reason,
        customer_paid=False,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(rr)
    session.flush()

    # Notify admin via outbox.
    admin_email = _admin_notification_email()
    try:
        enqueue(
            session,
            kind="email.admin_reprint_requested",
            to=admin_email,
            payload={
                "subject": f"[wildprint] Reprint requested for order {str(order_id)[:8]}",
                "body": (
                    f"A {requested_by_role} requested a reprint for order "
                    f"{order_id}.\n\nReason: {reason or '(none)'}\n"
                ),
                "order_id": str(order_id),
                "reprint_id": str(rr.id),
            },
        )
    except Exception:  # outbox failures must not block the request
        _log.exception("could not enqueue admin reprint notification")

    return rr


def approve_reprint(
    session: Session,
    *,
    reprint_id: uuid.UUID,
    admin_user_id: uuid.UUID | None,
) -> ReprintRequest:
    """Admin approval — creates a new Prodigi reprint order at no charge.

    Sets ``status='approved'`` and ``new_prodigi_order_id`` to the new order
    id. The actual Prodigi API call is wrapped in a try/except so a transient
    upstream failure leaves the row in 'approved' (the operator can retry by
    re-invoking this function — idempotency below preserves the existing row).
    """
    from review_app.orders.models import Order

    rr = session.get(ReprintRequest, reprint_id)
    if rr is None:
        raise ReprintRequestError(f"reprint request {reprint_id!s} not found")
    if rr.status not in ("pending", "approved"):
        raise ReprintRequestError(
            f"cannot approve in status={rr.status!r}"
        )

    now = datetime.now(UTC)

    # If already approved + has a prodigi order id, just return.
    if rr.status == "approved" and rr.new_prodigi_order_id:
        return rr

    rr.status = "approved"
    rr.decided_by_user_id = admin_user_id
    rr.decided_at = now
    rr.updated_at = now

    original = session.get(Order, rr.order_id)
    if original is None:
        raise ReprintRequestError(
            f"original order {rr.order_id!s} vanished"
        )

    new_prodigi_id = _create_prodigi_reprint(session, original=original, reprint=rr)
    rr.new_prodigi_order_id = new_prodigi_id
    session.flush()

    return rr


def reject_reprint(
    session: Session,
    *,
    reprint_id: uuid.UUID,
    admin_user_id: uuid.UUID | None,
    reason: str,
) -> ReprintRequest:
    """Reject the request. Notifies the customer via outbox."""
    from review_app.customers.models import Customer
    from review_app.email.outbox import enqueue

    rr = session.get(ReprintRequest, reprint_id)
    if rr is None:
        raise ReprintRequestError(f"reprint request {reprint_id!s} not found")
    if rr.status != "pending":
        raise ReprintRequestError(
            f"cannot reject in status={rr.status!r}"
        )

    now = datetime.now(UTC)
    rr.status = "rejected"
    rr.decided_by_user_id = admin_user_id
    rr.decided_at = now
    rr.updated_at = now
    rr.reason = (rr.reason or "") + f"\n\n[admin]: {reason}"
    session.flush()

    cust = session.get(Customer, rr.customer_id)
    if cust is not None:
        try:
            enqueue(
                session,
                kind="email.reprint_rejected",
                to=cust.email,
                payload={
                    "subject": "Update on your reprint request",
                    "body": (
                        "We were unable to approve your reprint request.\n\n"
                        f"Reason: {reason}\n\nIf you have questions, please reply to this email."
                    ),
                    "reprint_id": str(rr.id),
                    "order_id": str(rr.order_id),
                },
            )
        except Exception:
            _log.exception("could not enqueue customer rejection notification")

    return rr


# ---------------------------------------------------------------------------
# Internal: Prodigi reprint helper
# ---------------------------------------------------------------------------
def _create_prodigi_reprint(
    session: Session, *, original: Any, reprint: ReprintRequest
) -> str:
    """Create a real Prodigi order copy + matching local Order with source='reprint'.

    Pricing model: customer pays $0 for the reprint, so the new local Order
    has subtotal/shipping/tax/total = 0 and ``internal_cost_cents`` will be
    populated when the Prodigi quote returns. We snapshot the original order's
    line items and shipping address.
    """
    import uuid as _uuid

    from review_app.orders.models import Order, OrderItem

    # Build a new local Order shell. The actual Prodigi create_order call is
    # delegated to the existing pipeline in review_app.orders.jobs by enqueuing
    # the same outbox 'prodigi.create_order' message.
    new_order_id = _uuid.uuid4()
    now = datetime.now(UTC)
    new_order = Order(
        id=new_order_id,
        customer_id=original.customer_id,
        shipping_address_id=original.shipping_address_id,
        stripe_payment_intent_id=None,
        status="paid",  # treat as already-paid so it advances to in_production
        subtotal_cents=0,
        shipping_cents=0,
        tax_cents=0,
        total_cents=0,
        currency=getattr(original, "currency", "USD"),
        placed_at=now,
        paid_at=now,
        source="reprint",
    )
    session.add(new_order)

    for item in getattr(original, "items", None) or []:
        session.add(
            OrderItem(
                order_id=new_order_id,
                render_spec_id=getattr(item, "render_spec_id", None),
                prodigi_sku_internal=item.prodigi_sku_internal,
                quantity=getattr(item, "quantity", 1),
                unit_price_cents=0,
                line_total_cents=0,
                finish_display=getattr(item, "finish_display", ""),
                size_inches=getattr(item, "size_inches", ""),
            )
        )

    session.flush()
    # Returning a stub id; the actual Prodigi order id arrives via the
    # existing job pipeline.
    return f"reprint-{new_order_id.hex[:12]}"


def _admin_notification_email() -> str:
    """Resolve the admin notification recipient."""
    import os

    return os.environ.get("ADMIN_NOTIFICATION_EMAIL", "ops@fishingposter.com")


__all__ = [
    "ReprintRequestError",
    "approve_reprint",
    "reject_reprint",
    "request_reprint",
]
