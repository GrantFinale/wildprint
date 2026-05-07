"""Stripe webhook v2 — physical-product event fan-out.

Mounted at ``POST /webhook/stripe/v2``. The legacy ``POST /webhook/stripe``
in `review_app.app` continues to handle the $49 unlock; this endpoint owns
physical-product events and is independently signed.

Sequence inside the request handler:
  1. Verify signature (``stripe_client.construct_event_from_payload``).
  2. Open DB transaction.
  3. Insert ``stripe_events`` row (UNIQUE on event_id) — if duplicate,
     return 200 immediately.
  4. Dispatch to the typed event handler.
  5. Persist Order + OrderItems via ``orders.service.place_order_from_cart``.
  6. Insert outbox rows for ``prodigi.create_order``, ``render.tier_3``
     (one per item), and ``email.order_confirmed``. NO direct external
     calls inside the handler — all side effects are async via the outbox.
  7. Mark the event row processed.
  8. COMMIT.
  9. Return 200.

If anything in steps 4-7 raises, we attempt to mark ``processed_status='error'``
and return 500 so Stripe retries.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, jsonify, make_response, request
from sqlalchemy.exc import IntegrityError

from review_app.checkout import stripe_client
from review_app.checkout.stripe_events import StripeEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_log = logging.getLogger(__name__)

webhook_bp = Blueprint("stripe_webhook_v2", __name__)


# ---------------------------------------------------------------------------
# DB session helpers (mirror cart/routes)
# ---------------------------------------------------------------------------
def _get_session() -> "Session":
    from flask import g

    existing = getattr(g, "db", None)
    if existing is not None:
        return existing  # type: ignore[no-any-return]

    from review_app.db import get_session_factory

    session = get_session_factory()()
    g.db = session
    g.db_owned_by_request = True
    return session


def _close_session_if_owned(session: "Session", commit: bool) -> None:
    from flask import g

    if not getattr(g, "db_owned_by_request", False):
        return
    try:
        if commit:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()
        g.db = None
        g.db_owned_by_request = False


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@webhook_bp.route("/webhook/stripe/v2", methods=["POST"])
def stripe_webhook_v2() -> Response:
    """Handle Stripe webhook events for the physical-product flow."""
    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe_client.construct_event_from_payload(payload, sig_header)
    except stripe_client.StripeNotConfiguredError as exc:
        _log.error("stripe webhook v2 not configured: %s", exc)
        return make_response(jsonify({"error": str(exc)}), 503)
    except stripe_client.StripeSignatureError as exc:
        _log.warning("stripe webhook v2 invalid signature: %s", exc)
        return make_response(jsonify({"error": "invalid signature"}), 400)
    except Exception as exc:  # noqa: BLE001
        _log.exception("stripe webhook v2 verification crashed")
        return make_response(jsonify({"error": "verification failed", "detail": str(exc)}), 400)

    event_id = event.get("id") or ""
    event_type = event.get("type") or ""
    if not event_id or not event_type:
        return make_response(jsonify({"error": "event missing id/type"}), 400)

    session = _get_session()
    committed = False
    try:
        # Step 3 — dedup by event_id. Try insert; if UNIQUE violation, treat
        # as a duplicate delivery and return 200.
        ev_row = StripeEvent(
            event_id=event_id,
            event_type=event_type,
            raw_payload=event,
        )
        session.add(ev_row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            _log.info("stripe webhook v2 duplicate event_id=%s", event_id)
            committed = True  # nothing to commit, but don't roll back the close path
            return make_response(jsonify({"status": "duplicate", "event_id": event_id}), 200)

        # Step 4 — dispatch. We tolerate unknown event types (return 200 so
        # Stripe stops retrying); only mark error for true exceptions.
        try:
            _dispatch_event(session, event)
        except Exception as exc:  # noqa: BLE001
            _log.exception("stripe webhook v2 handler crashed for event_id=%s", event_id)
            ev_row.processed_status = "error"
            ev_row.processed_at = datetime.now(UTC)
            ev_row.error_message = f"{type(exc).__name__}: {exc}"
            session.flush()
            session.commit()
            committed = True
            return make_response(
                jsonify({"error": "handler failed", "event_id": event_id}), 500
            )

        ev_row.processed_status = "ok"
        ev_row.processed_at = datetime.now(UTC)
        session.flush()
        committed = True
        return make_response(jsonify({"status": "ok", "event_id": event_id}), 200)
    finally:
        _close_session_if_owned(session, commit=committed)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def _dispatch_event(session: "Session", event: dict[str, Any]) -> None:
    """Route by ``event['type']``. Unknown events are silently OK."""
    handlers: dict[str, Any] = {
        "checkout.session.completed": _handle_checkout_session_completed,
        "payment_intent.succeeded": _handle_payment_intent_succeeded,
        "payment_intent.payment_failed": _handle_payment_intent_failed,
        "charge.refunded": _handle_charge_refunded,
    }
    event_type = event.get("type") or ""
    handler = handlers.get(event_type)
    if handler is None:
        _log.info("stripe webhook v2: ignoring event type=%s", event_type)
        return
    handler(session, event)


# ---------------------------------------------------------------------------
# Event handlers — each idempotent, each delegates side effects to the outbox
# ---------------------------------------------------------------------------
def _handle_checkout_session_completed(session: "Session", event: dict[str, Any]) -> None:
    """Persist Order + OrderItems and enqueue prodigi.create_order + render.tier_3 + email."""
    from review_app.cart import service as cart_service
    from review_app.cart.models import Cart
    from review_app.customers.models import Customer
    from review_app.email.outbox import enqueue
    from review_app.orders.service import place_order_from_cart

    cs = (event.get("data") or {}).get("object") or {}
    metadata = cs.get("metadata") or {}

    cart_id_raw = metadata.get("cart_id")
    shipping_address_id_raw = metadata.get("shipping_address_id")
    if not cart_id_raw or not shipping_address_id_raw:
        _log.warning(
            "checkout.session.completed missing metadata: cart_id=%r shipping_address_id=%r",
            cart_id_raw, shipping_address_id_raw,
        )
        return
    cart_id = uuid.UUID(cart_id_raw)
    shipping_address_id = uuid.UUID(shipping_address_id_raw)

    email = (
        metadata.get("customer_email_lower")
        or cs.get("customer_email")
        or (cs.get("customer_details") or {}).get("email")
        or ""
    ).strip().lower()

    payment_intent_id = cs.get("payment_intent")
    if not payment_intent_id:
        _log.warning("checkout.session.completed missing payment_intent")
        return

    if not email:
        _log.warning("checkout.session.completed missing customer email")
        return

    # Look up / create the customer.
    customer = Customer.get_active_by_email(session, email)
    if customer is None:
        customer = Customer.create(
            email=email,
            marketing_opt_in=(metadata.get("marketing_opt_in") == "1"),
        )
        session.add(customer)
        session.flush()

    cart = session.get(Cart, cart_id)
    if cart is None:
        _log.error("checkout.session.completed: cart %s not found", cart_id)
        return

    # Snapshot Stripe-provided shipping/tax (in cents) if present.
    shipping_cents = int((cs.get("shipping_cost") or {}).get("amount_total") or 0)
    tax_cents = int((cs.get("total_details") or {}).get("amount_tax") or 0)

    order = place_order_from_cart(
        session,
        cart=cart,
        customer_id=customer.id,
        shipping_address_id=shipping_address_id,
        stripe_payment_intent_id=payment_intent_id,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
    )

    # Enqueue the outbox fan-out. All three ride on the same DB transaction
    # as the order persistence above so partial failures don't lose work.
    enqueue(
        session,
        kind="prodigi.create_order",
        to=email,
        payload={"order_id": str(order.id)},
    )
    for item in order.items:
        enqueue(
            session,
            kind="render.tier_3",
            to=email,
            payload={
                "order_id": str(order.id),
                "order_item_id": str(item.id),
                "render_spec_id": (
                    str(item.render_spec_id) if item.render_spec_id else ""
                ),
            },
        )
    enqueue(
        session,
        kind="email.order_confirmed",
        to=email,
        payload={
            "order_id": str(order.id),
            "total_cents": order.total_cents,
            "item_count": sum(it.quantity for it in order.items),
        },
    )


def _handle_payment_intent_succeeded(session: "Session", event: dict[str, Any]) -> None:
    """Mark the order ``paid`` (no-op if already paid). Idempotent by PI id."""
    from sqlalchemy import select

    from review_app.email.outbox import OutboxEntry, enqueue
    from review_app.orders.models import Order

    pi = (event.get("data") or {}).get("object") or {}
    pi_id = pi.get("id")
    if not pi_id:
        return

    stmt = select(Order).where(Order.stripe_payment_intent_id == pi_id)
    order = session.execute(stmt).scalar_one_or_none()
    if order is None:
        # The order might not yet have been written by checkout.session.completed
        # (Stripe doesn't guarantee delivery order). That's fine — when the
        # session-completed event lands it will mark status='paid' anyway.
        _log.info("payment_intent.succeeded: order for PI %s not yet inserted", pi_id)
        return

    if order.status != "paid":
        order.status = "paid"
        if order.paid_at is None:
            order.paid_at = datetime.now(UTC)
        session.flush()

    # Belt-and-braces: re-enqueue the confirmation email if no prior outbox
    # row exists for this order yet.
    existing_email_stmt = select(OutboxEntry).where(
        OutboxEntry.kind == "email.order_confirmed",
    )
    found = False
    for row in session.execute(existing_email_stmt).scalars().all():
        payload = row.payload or {}
        if payload.get("order_id") == str(order.id):
            found = True
            break
    if not found:
        recipient = (
            pi.get("receipt_email")
            or (pi.get("charges") or {}).get("data", [{}])[0].get("billing_details", {}).get("email")
            or ""
        )
        if recipient:
            enqueue(
                session,
                kind="email.order_confirmed",
                to=recipient,
                payload={
                    "order_id": str(order.id),
                    "total_cents": order.total_cents,
                },
            )


def _handle_payment_intent_failed(session: "Session", event: dict[str, Any]) -> None:
    """Mark the order ``cancelled`` and enqueue a payment_failed email."""
    from sqlalchemy import select

    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order

    pi = (event.get("data") or {}).get("object") or {}
    pi_id = pi.get("id")
    if not pi_id:
        return

    stmt = select(Order).where(Order.stripe_payment_intent_id == pi_id)
    order = session.execute(stmt).scalar_one_or_none()
    if order is None:
        _log.info("payment_intent.payment_failed: no order for PI %s", pi_id)
        return

    if order.status not in ("cancelled", "refunded"):
        order.status = "cancelled"
        session.flush()

    recipient = (
        pi.get("receipt_email")
        or (pi.get("charges") or {}).get("data", [{}])[0].get("billing_details", {}).get("email")
        or ""
    )
    if recipient:
        # email.payment_failed isn't in the Phase 0.5 template registry yet.
        # Wrap with try so a missing template doesn't block the webhook —
        # we still mark the order cancelled.
        try:
            enqueue(
                session,
                kind="email.payment_failed",
                to=recipient,
                payload={
                    "order_id": str(order.id),
                    "failure_message": pi.get("last_payment_error", {}).get("message", ""),
                },
            )
        except Exception:  # noqa: BLE001
            _log.warning("could not enqueue email.payment_failed (kind missing?)")


def _handle_charge_refunded(session: "Session", event: dict[str, Any]) -> None:
    """Stripe refund event landed (initiated from dashboard or our own /refunds).

    For Phase 3b we mirror the refund row + flip status. The full refund
    flow (Prodigi cancel attempt + email) lives in
    :mod:`review_app.refunds.service` for refunds we initiate; this handler
    handles dashboard-initiated refunds we didn't originate.
    """
    from sqlalchemy import select

    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order
    from review_app.refunds.models import Refund

    charge = (event.get("data") or {}).get("object") or {}
    pi_id = charge.get("payment_intent")
    if not pi_id:
        return

    stmt = select(Order).where(Order.stripe_payment_intent_id == pi_id)
    order = session.execute(stmt).scalar_one_or_none()
    if order is None:
        return

    refunds = charge.get("refunds") or {}
    refund_data = (refunds.get("data") or [{}])[0]
    stripe_refund_id = refund_data.get("id") or charge.get("id")
    refund_amount_cents = int(refund_data.get("amount") or charge.get("amount_refunded") or 0)
    if refund_amount_cents <= 0:
        return

    # Idempotency by stripe_refund_id.
    existing_stmt = select(Refund).where(Refund.stripe_refund_id == stripe_refund_id)
    existing = session.execute(existing_stmt).scalar_one_or_none()
    if existing is not None:
        return

    refund = Refund(
        order_id=order.id,
        stripe_refund_id=stripe_refund_id,
        amount_cents=refund_amount_cents,
        status="succeeded",
        reason=refund_data.get("reason"),
    )
    session.add(refund)
    if order.status not in ("refunded", "cancelled"):
        order.status = "refunded"
    session.flush()

    recipient = charge.get("billing_details", {}).get("email") or charge.get("receipt_email")
    if recipient:
        try:
            enqueue(
                session,
                kind="email.refunded",
                to=recipient,
                payload={
                    "order_id": str(order.id),
                    "amount_cents": refund_amount_cents,
                },
            )
        except Exception:  # noqa: BLE001
            _log.warning("could not enqueue email.refunded")


__all__ = ["stripe_webhook_v2", "webhook_bp"]
