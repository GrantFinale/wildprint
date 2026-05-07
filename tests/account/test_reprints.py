"""Tests for review_app.refunds.reprints workflow."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from review_app.refunds.reprints import (
    ReprintRequestError,
    approve_reprint,
    reject_reprint,
    request_reprint,
)
from review_app.refunds.reprints_models import ReprintRequest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture()
def order_setup(db_session: Session) -> dict[str, Any]:
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order, OrderItem

    cust = Customer.create(email="repcust@example.com")
    db_session.add(cust)
    db_session.flush()

    addr = Address(
        customer_id=cust.id,
        line1="1 Reprint Way",
        city="Boston",
        state="MA",
        zip="02101",
        country="US",
    )
    db_session.add(addr)
    db_session.flush()

    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_test_repr",
        status="delivered",
        subtotal_cents=5000,
        shipping_cents=0,
        tax_cents=0,
        total_cents=5000,
        currency="USD",
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
        delivered_at=datetime.now(UTC),
    )
    db_session.add(order)
    db_session.flush()

    item = OrderItem(
        order_id=order.id,
        prodigi_sku_internal="GLOBAL-CFPM-16x20-PHO-FRA",
        quantity=1,
        unit_price_cents=5000,
        line_total_cents=5000,
        finish_display="Black",
        size_inches="16x20",
    )
    db_session.add(item)
    db_session.flush()
    return {"customer": cust, "order": order, "address": addr}


def test_request_reprint_creates_pending_row(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    rr = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason="bent corner",
        line_item_ids=None,
    )
    assert rr.status == "pending"
    assert rr.requested_by_role == "customer"


def test_request_reprint_idempotent(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    rr1 = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason="x",
        line_item_ids=None,
    )
    rr2 = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason="y",
        line_item_ids=None,
    )
    assert rr1.id == rr2.id


def test_request_reprint_wrong_customer(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    with pytest.raises(ReprintRequestError):
        request_reprint(
            db_session,
            order_id=order_setup["order"].id,
            customer_id=uuid.uuid4(),
            reason=None,
            line_item_ids=None,
        )


def test_approve_reprint_creates_new_order(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    from review_app.orders.models import Order

    rr = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason="quality issue",
        line_item_ids=None,
    )
    approved = approve_reprint(
        db_session, reprint_id=rr.id, admin_user_id=None
    )
    assert approved.status == "approved"
    assert approved.new_prodigi_order_id is not None

    # New local Order with source='reprint' was created.
    new_orders = (
        db_session.query(Order).filter(Order.source == "reprint").all()
    )
    assert len(new_orders) == 1
    assert new_orders[0].total_cents == 0


def test_reject_reprint_sets_rejected(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    rr = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason=None,
        line_item_ids=None,
    )
    rejected = reject_reprint(
        db_session,
        reprint_id=rr.id,
        admin_user_id=None,
        reason="out of policy",
    )
    assert rejected.status == "rejected"
    assert "[admin]" in (rejected.reason or "")


def test_cannot_reject_approved(
    db_session: Session, order_setup: dict[str, Any]
) -> None:
    rr = request_reprint(
        db_session,
        order_id=order_setup["order"].id,
        customer_id=order_setup["customer"].id,
        reason=None,
        line_item_ids=None,
    )
    approve_reprint(db_session, reprint_id=rr.id, admin_user_id=None)
    with pytest.raises(ReprintRequestError):
        reject_reprint(
            db_session,
            reprint_id=rr.id,
            admin_user_id=None,
            reason="too late",
        )
