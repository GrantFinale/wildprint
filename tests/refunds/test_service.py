"""Unit tests for review_app.refunds.service."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from review_app.refunds import service as refunds_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def order_with_prodigi(db_session: Session) -> dict[str, Any]:
    """Build an Order + a ProdigiOrder row tied to it.

    Sets up the SQLite path: prodigi_orders.fishingposter_order_id is stored
    as a hex string under SQLite (variant), so we use uuid.hex.
    """
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order
    from review_app.prodigi.db_models import ProdigiOrder

    cust = Customer.create(email="refundee@example.com")
    db_session.add(cust)
    db_session.flush()

    addr = Address(
        customer_id=cust.id,
        line1="1 Refund Way",
        city="Boston",
        state="MA",
        zip="02101",
        validated_at=datetime.now(UTC),
        validation_provider="smarty",
        dpv_match_code="Y",
    )
    db_session.add(addr)
    db_session.flush()

    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_refund_001",
        status="paid",
        subtotal_cents=12900,
        total_cents=12900,
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    db_session.add(order)
    db_session.flush()

    prow = ProdigiOrder(
        fishingposter_order_id=order.id.hex,
        prodigi_order_id="ord_test_refund_123",
        idempotency_key=f"wp-{order.id}",
        status_stage="InProgress",
    )
    db_session.add(prow)
    db_session.flush()

    return {"order": order, "customer": cust, "prodigi_order": prow}


@pytest.fixture()
def patched_externals(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch Stripe + Prodigi clients with mocks the tests can introspect."""
    fake_stripe_refund = {"id": "re_test_001", "status": "succeeded"}
    stripe_mock = MagicMock(return_value=fake_stripe_refund)
    monkeypatch.setattr(
        "review_app.checkout.stripe_client.create_refund", stripe_mock
    )

    fake_prodigi_client = MagicMock()
    monkeypatch.setattr(
        "review_app.prodigi.get_default_client", lambda: fake_prodigi_client
    )
    return {"stripe": stripe_mock, "prodigi": fake_prodigi_client}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_request_refund_succeeds_when_prodigi_cancellable(
    order_with_prodigi: dict[str, Any],
    patched_externals: dict[str, MagicMock],
    db_session: Session,
) -> None:
    """Happy path: Prodigi cancels OK, Stripe refunds OK, status='succeeded'."""
    order = order_with_prodigi["order"]
    refund = refunds_service.request_refund(
        db_session,
        order_id=order.id,
        amount_cents=12900,
        reason="customer changed mind",
        requested_by_user_id=None,
    )
    assert refund.status == "succeeded"
    assert refund.stripe_refund_id == "re_test_001"
    assert refund.prodigi_cancel_attempted is True
    assert refund.prodigi_cancel_succeeded is True
    patched_externals["prodigi"].cancel_order.assert_called_once_with(
        "ord_test_refund_123"
    )
    patched_externals["stripe"].assert_called_once()


def test_request_refund_proceeds_with_stripe_when_prodigi_cancel_window_passed(
    order_with_prodigi: dict[str, Any],
    patched_externals: dict[str, MagicMock],
    db_session: Session,
) -> None:
    """Prodigi rejects cancel (already in production) — Stripe still refunds."""
    from review_app.prodigi.client import ProdigiClientError

    patched_externals["prodigi"].cancel_order.side_effect = ProdigiClientError(
        "order already in production",
        status_code=409,
    )
    order = order_with_prodigi["order"]
    refund = refunds_service.request_refund(
        db_session,
        order_id=order.id,
        amount_cents=12900,
        reason="please refund",
        requested_by_user_id=None,
    )
    assert refund.status == "succeeded"
    assert refund.prodigi_cancel_attempted is True
    assert refund.prodigi_cancel_succeeded is False
    patched_externals["stripe"].assert_called_once()


def test_request_refund_idempotent(
    order_with_prodigi: dict[str, Any],
    patched_externals: dict[str, MagicMock],
    db_session: Session,
) -> None:
    """Calling twice on an already-refunded order returns the existing row."""
    order = order_with_prodigi["order"]
    refund1 = refunds_service.request_refund(
        db_session,
        order_id=order.id,
        amount_cents=12900,
        reason="first call",
        requested_by_user_id=None,
    )
    # The order's status is now 'refunded'. A second call should return the
    # same Refund row without issuing another Stripe refund.
    refund2 = refunds_service.request_refund(
        db_session,
        order_id=order.id,
        amount_cents=12900,
        reason="second call",
        requested_by_user_id=None,
    )
    assert refund2.id == refund1.id
    # Stripe refund creation only happened on the first call.
    assert patched_externals["stripe"].call_count == 1


def test_request_refund_fails_loudly_when_stripe_rejects(
    order_with_prodigi: dict[str, Any],
    patched_externals: dict[str, MagicMock],
    db_session: Session,
) -> None:
    """Stripe refund failure → StripeRefundFailedError + refund.status='failed'."""
    from sqlalchemy import select

    from review_app.refunds.models import Refund

    patched_externals["stripe"].side_effect = RuntimeError("card not chargeable")

    order = order_with_prodigi["order"]
    with pytest.raises(refunds_service.StripeRefundFailedError):
        refunds_service.request_refund(
            db_session,
            order_id=order.id,
            amount_cents=12900,
            reason=None,
            requested_by_user_id=None,
        )

    refund = db_session.execute(
        select(Refund).where(Refund.order_id == order.id)
    ).scalar_one()
    assert refund.status == "failed"


def test_request_refund_handles_no_prodigi_order(
    db_session: Session,
    patched_externals: dict[str, MagicMock],
) -> None:
    """Refund initiated before Prodigi order was created — Stripe still refunds,
    prodigi_cancel_succeeded is None."""
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order

    cust = Customer.create(email="early@example.com")
    db_session.add(cust)
    db_session.flush()
    addr = Address(
        customer_id=cust.id,
        line1="x",
        city="x",
        state="OR",
        zip="97201",
        validated_at=datetime.now(UTC),
        validation_provider="smarty",
        dpv_match_code="Y",
    )
    db_session.add(addr)
    db_session.flush()
    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_early_001",
        status="paid",
        subtotal_cents=1000,
        total_cents=1000,
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    db_session.add(order)
    db_session.flush()

    refund = refunds_service.request_refund(
        db_session,
        order_id=order.id,
        amount_cents=1000,
        reason=None,
        requested_by_user_id=None,
    )
    assert refund.status == "succeeded"
    assert refund.prodigi_cancel_attempted is True
    assert refund.prodigi_cancel_succeeded is None
    patched_externals["prodigi"].cancel_order.assert_not_called()
