"""Order + OrderItem model tests.

Asserts the math invariants and CHECK-constraint enforcement.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from review_app.customers.models import Customer
from review_app.orders.models import Order, OrderItem


def _bootstrap_sku(db_session: Session) -> str:
    """Insert a minimal prodigi_skus row to satisfy FK constraints. Returns
    the internal_sku."""
    from sqlalchemy import insert

    from review_app.prodigi.db_models import ProdigiSku

    sku = "test-cf-12x16-natural"
    db_session.execute(
        insert(ProdigiSku).values(
            internal_sku=sku,
            prodigi_sku="GLOBAL-CFPM-12X16",
            finish="Natural",
            size_inches="12x16",
            orientation="portrait",
        )
    )
    db_session.flush()
    return sku


def _bootstrap_customer(db_session: Session, email: str = "buyer@example.com") -> Customer:
    cust = Customer.create(email=email)
    db_session.add(cust)
    db_session.flush()
    return cust


def test_order_total_cents_matches_subtotal_plus_shipping_plus_tax(
    db_session: Session,
) -> None:
    """The pure assertion helper used by post-save invariants."""
    cust = _bootstrap_customer(db_session)
    o = Order(
        customer_id=cust.id,
        subtotal_cents=4000,
        shipping_cents=900,
        tax_cents=350,
        total_cents=5250,
        currency="USD",
    )
    assert o.total_matches_components() is True

    o.tax_cents = 351
    assert o.total_matches_components() is False


def test_order_status_check_constraint_rejects_invalid(
    db_session: Session,
) -> None:
    """status='banana' should be rejected by the CHECK constraint."""
    cust = _bootstrap_customer(db_session, email="bad@example.com")
    bad = Order(
        customer_id=cust.id,
        status="banana",
        subtotal_cents=100,
        shipping_cents=0,
        tax_cents=0,
        total_cents=100,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_order_item_line_total_matches_unit_x_qty() -> None:
    """Pure helper: line_total_cents must equal unit_price_cents * quantity."""
    item = OrderItem(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        prodigi_sku_internal="cf-12x16-natural",
        quantity=3,
        unit_price_cents=2000,
        line_total_cents=6000,
        finish_display="Natural",
        size_inches="12x16",
    )
    assert item.line_total_matches() is True

    item.quantity = 4
    assert item.line_total_matches() is False


def test_order_status_accepts_all_valid_states(db_session: Session) -> None:
    """All values in VALID_ORDER_STATUSES must satisfy the CHECK."""
    from review_app.orders.models import VALID_ORDER_STATUSES

    cust = _bootstrap_customer(db_session, email="states@example.com")
    for status in VALID_ORDER_STATUSES:
        o = Order(
            customer_id=cust.id,
            status=status,
            subtotal_cents=100,
            shipping_cents=0,
            tax_cents=0,
            total_cents=100,
        )
        db_session.add(o)
    db_session.flush()


def test_order_item_quantity_must_be_positive(db_session: Session) -> None:
    """quantity=0 violates ck_order_items_quantity_positive."""
    cust = _bootstrap_customer(db_session, email="qtytest@example.com")
    sku = _bootstrap_sku(db_session)
    o = Order(
        customer_id=cust.id,
        status="paid",
        subtotal_cents=0,
        shipping_cents=0,
        tax_cents=0,
        total_cents=0,
    )
    db_session.add(o)
    db_session.flush()

    item = OrderItem(
        order_id=o.id,
        prodigi_sku_internal=sku,
        quantity=0,
        unit_price_cents=0,
        line_total_cents=0,
        finish_display="Natural",
        size_inches="12x16",
    )
    db_session.add(item)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
