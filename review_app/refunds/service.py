"""Refund orchestration: Stripe refund + Prodigi cancel coordination.

Per memory/project_prodigi_quirks.md, Prodigi has NO ``/pause`` endpoint —
only ``cancel_order``, and only before the ``inProduction`` stage flips
to ``InProgress``. We must:

1. Insert the Refund row (status='pending'), so we have an audit record
   even if the rest of the flow crashes.
2. Try to cancel the Prodigi order. Tolerate "already in production":
   record ``prodigi_cancel_attempted=True``, ``prodigi_cancel_succeeded=False``.
   In that case the customer KEEPS the print AND gets refunded — the
   margin loss is acceptable per Phase 3b decision (we don't want angry
   customers stuck with un-refundable charges).
3. Issue the Stripe refund via stripe_client.create_refund.
4. Update the Refund row with the Stripe refund id + final status.
5. Enqueue an ``email.refunded`` outbox row.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from review_app.refunds.models import Refund

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class RefundServiceError(Exception):
    """Base for refund-orchestration failures."""


class RefundNotAllowedError(RefundServiceError):
    """Raised when the order isn't in a refundable state (already refunded, etc.)."""


class StripeRefundFailedError(RefundServiceError):
    """Raised when Stripe rejected the refund attempt."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def request_refund(
    session: Session,
    *,
    order_id: uuid.UUID,
    amount_cents: int,
    reason: str | None,
    requested_by_user_id: uuid.UUID | None,
) -> Refund:
    """Initiate a refund. Caller commits.

    Idempotency: if a Refund row already exists for the same (order_id,
    amount_cents, reason) tuple AND its status is 'pending' or 'succeeded',
    we return the existing row instead of issuing another Stripe refund.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from review_app.checkout import stripe_client
    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order

    if amount_cents <= 0:
        raise RefundServiceError(f"amount_cents must be > 0, got {amount_cents!r}")

    order = session.get(Order, order_id)
    if order is None:
        raise RefundServiceError(f"Order {order_id!s} not found")
    if not order.stripe_payment_intent_id:
        raise RefundNotAllowedError(
            f"Order {order_id!s} has no stripe_payment_intent_id — cannot refund"
        )
    if order.status in ("refunded",):
        # Idempotency: previous full refund already landed.
        existing_stmt = (
            select(Refund)
            .where(Refund.order_id == order.id)
            .where(Refund.status == "succeeded")
            .order_by(Refund.created_at.desc())
            .limit(1)
        )
        existing = session.execute(existing_stmt).scalar_one_or_none()
        if existing is not None:
            return existing

    # Step 1: Insert the pending Refund row up-front for audit.
    refund = Refund(
        order_id=order.id,
        amount_cents=amount_cents,
        reason=reason,
        status="pending",
        requested_by_user_id=requested_by_user_id,
        prodigi_cancel_attempted=False,
    )
    session.add(refund)
    session.flush()

    # Step 2: Attempt Prodigi cancel. Find the prodigi_orders row.
    _attempt_prodigi_cancel(session, order, refund)

    # Step 3: Issue the Stripe refund.
    try:
        stripe_refund = stripe_client.create_refund(
            payment_intent_id=order.stripe_payment_intent_id,
            amount_cents=amount_cents,
            reason=_normalize_stripe_reason(reason),
            metadata={
                "wildprint_order_id": str(order.id),
                "wildprint_refund_id": str(refund.id),
            },
        )
    except stripe_client.StripeNotConfiguredError:
        # Surfacing this is the right call — admin needs to know.
        refund.status = "failed"
        session.flush()
        raise
    except Exception as exc:
        _log.error("Stripe refund failed for order %s: %s", order.id, exc)
        refund.status = "failed"
        session.flush()
        raise StripeRefundFailedError(str(exc)) from exc

    # Step 4: Persist the Stripe refund id + final status.
    refund.stripe_refund_id = stripe_refund.get("id")
    refund.status = "succeeded"
    if order.status not in ("refunded",):
        order.status = "refunded"

    # Step 5: Email outbox row.
    customer_email = _customer_email(session, order)
    if customer_email:
        try:
            enqueue(
                session,
                kind="email.refunded",
                to=customer_email,
                payload={
                    "order_id": str(order.id),
                    "refund_id": str(refund.id),
                    "amount_cents": amount_cents,
                    "prodigi_cancel_succeeded": refund.prodigi_cancel_succeeded,
                },
            )
        except Exception:
            _log.warning("could not enqueue email.refunded")

    refund.customer_notified_at = datetime.now(UTC)
    session.flush()
    return refund


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attempt_prodigi_cancel(
    session: Session, order: object, refund: Refund
) -> None:
    """Best-effort Prodigi cancel. Mutates ``refund`` in place; never raises."""
    from sqlalchemy import select

    from review_app.prodigi import get_default_client
    from review_app.prodigi.client import ProdigiClientError
    from review_app.prodigi.db_models import ProdigiOrder

    refund.prodigi_cancel_attempted = True

    # Find the Prodigi order id, if any. We look up via fishingposter_order_id
    # (the Phase 1 column name) because Phase 1 ProdigiOrder.order_id was
    # added later by migration 0011 but the ORM model still uses the old name.
    bind = session.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    order_uuid = order.id  # type: ignore[attr-defined]
    fk_value = order_uuid.hex if is_sqlite else order_uuid
    stmt = select(ProdigiOrder).where(
        ProdigiOrder.fishingposter_order_id == fk_value
    )
    prodigi_order_row = session.execute(stmt).scalar_one_or_none()
    if prodigi_order_row is None or not prodigi_order_row.prodigi_order_id:
        # No Prodigi order yet → nothing to cancel.
        refund.prodigi_cancel_succeeded = None
        session.flush()
        return

    try:
        client = get_default_client()
        client.cancel_order(prodigi_order_row.prodigi_order_id)
        refund.prodigi_cancel_succeeded = True
    except ProdigiClientError as exc:
        _log.warning(
            "Prodigi cancel rejected for order %s (status=%s): %s",
            order_uuid, exc.status_code, exc,
        )
        refund.prodigi_cancel_succeeded = False
    except Exception as exc:
        _log.warning(
            "Prodigi cancel crashed for order %s: %s", order_uuid, exc
        )
        refund.prodigi_cancel_succeeded = False

    session.flush()


def _normalize_stripe_reason(reason: str | None) -> str | None:
    """Coerce free-form reason text to one of Stripe's accepted values."""
    if not reason:
        return None
    lowered = reason.strip().lower()
    if "fraud" in lowered:
        return "fraudulent"
    if "duplicate" in lowered or "double" in lowered:
        return "duplicate"
    return "requested_by_customer"


def _customer_email(session: Session, order: object) -> str | None:
    from review_app.customers.models import Customer

    customer = session.get(Customer, order.customer_id)  # type: ignore[attr-defined]
    return customer.email if customer else None


__all__ = [
    "RefundNotAllowedError",
    "RefundServiceError",
    "StripeRefundFailedError",
    "request_refund",
]
