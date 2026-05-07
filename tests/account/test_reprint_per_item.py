"""Per-line-item reprint tests (Phase 6 polish).

Covers the customer-facing POST /account/orders/<id>/reprint route's
new contract: the form posts ``order_item_ids`` as a multi-valued field
+ ``reason`` (selector) + ``comment`` (free text).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from review_app.customers.models import Customer

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


@pytest.fixture()
def order_with_two_items(
    account_app: Flask,
    account_client: FlaskClient,
    account_db_session: Session,
) -> tuple[FlaskClient, Customer, list]:
    """Sign in a customer + seed a delivered order with two line items."""
    from review_app.addresses.models import Address
    from review_app.orders.models import Order, OrderItem

    cust = Customer.create(email="reprint-perline@example.com", name="Per Item")
    account_db_session.add(cust)
    account_db_session.commit()

    addr = Address(
        customer_id=cust.id,
        line1="1 Reprint Way",
        city="Boston",
        state="MA",
        zip="02101",
        country="US",
    )
    account_db_session.add(addr)
    account_db_session.commit()

    now = datetime.now(UTC)
    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_perline_test",
        status="delivered",
        subtotal_cents=10000,
        shipping_cents=0,
        tax_cents=0,
        total_cents=10000,
        currency="USD",
        placed_at=now - timedelta(days=10),
        paid_at=now - timedelta(days=10),
        delivered_at=now - timedelta(days=2),
    )
    account_db_session.add(order)
    account_db_session.commit()

    items = []
    for size, finish in (("16x20", "Black"), ("11x14", "Natural")):
        oi = OrderItem(
            order_id=order.id,
            prodigi_sku_internal=f"GLOBAL-{size}-{finish.upper()}",
            quantity=1,
            unit_price_cents=5000,
            line_total_cents=5000,
            finish_display=finish,
            size_inches=size,
        )
        account_db_session.add(oi)
        items.append(oi)
    account_db_session.commit()

    with account_client.session_transaction() as sess:
        sess["customer_id"] = str(cust.id)
    return account_client, cust, items


def test_reprint_post_with_selected_item_creates_request(
    order_with_two_items: tuple[FlaskClient, Customer, list],
    account_db_session: Session,
) -> None:
    """POST with one item id creates a ReprintRequest and stores its id."""
    from sqlalchemy import select

    from review_app.refunds.reprints_models import ReprintRequest

    client, _cust, items = order_with_two_items
    item_id = str(items[0].id)
    resp = client.post(
        f"/account/orders/{items[0].order_id}/reprint",
        data={
            "order_item_ids": [item_id],
            "reason": "warped",
            "comment": "noticed the curl after a week",
        },
        follow_redirects=False,
    )
    assert resp.status_code in {302, 303}

    rr = account_db_session.execute(select(ReprintRequest)).scalar_one_or_none()
    assert rr is not None
    assert rr.status == "pending"
    assert item_id in (rr.line_item_ids or "")


def test_reprint_post_with_no_selection_redirects_with_error(
    order_with_two_items: tuple[FlaskClient, Customer, list],
    account_db_session: Session,
) -> None:
    """POST without any order_item_ids should NOT create a request."""
    from sqlalchemy import select

    from review_app.refunds.reprints_models import ReprintRequest

    client, _cust, items = order_with_two_items
    resp = client.post(
        f"/account/orders/{items[0].order_id}/reprint",
        data={"reason": "warped", "comment": ""},
        follow_redirects=False,
    )
    assert resp.status_code in {302, 303}
    rr = account_db_session.execute(select(ReprintRequest)).scalar_one_or_none()
    assert rr is None


def test_reprint_post_age_limit_rejects_old_orders(
    account_app: Flask,
    account_client: FlaskClient,
    account_db_session: Session,
) -> None:
    """An order delivered >30 days ago is ineligible — handler rejects."""
    from sqlalchemy import select

    from review_app.addresses.models import Address
    from review_app.orders.models import Order, OrderItem
    from review_app.refunds.reprints_models import ReprintRequest

    cust = Customer.create(email="ancient@example.com")
    account_db_session.add(cust)
    account_db_session.commit()
    addr = Address(
        customer_id=cust.id, line1="Old", city="X", state="MA",
        zip="02101", country="US",
    )
    account_db_session.add(addr)
    account_db_session.commit()
    old = datetime.now(UTC) - timedelta(days=90)
    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_old",
        status="delivered",
        subtotal_cents=1000,
        shipping_cents=0,
        tax_cents=0,
        total_cents=1000,
        placed_at=old,
        paid_at=old,
        delivered_at=old,
    )
    account_db_session.add(order)
    account_db_session.commit()
    item = OrderItem(
        order_id=order.id,
        prodigi_sku_internal="GLOBAL-OLD",
        quantity=1,
        unit_price_cents=1000,
        line_total_cents=1000,
        finish_display="Black",
        size_inches="16x20",
    )
    account_db_session.add(item)
    account_db_session.commit()

    with account_client.session_transaction() as sess:
        sess["customer_id"] = str(cust.id)

    resp = account_client.post(
        f"/account/orders/{order.id}/reprint",
        data={"order_item_ids": [str(item.id)], "reason": "warped"},
        follow_redirects=False,
    )
    assert resp.status_code in {302, 303}
    rr = account_db_session.execute(select(ReprintRequest)).scalar_one_or_none()
    assert rr is None


def test_reprint_post_duplicate_blocked(
    order_with_two_items: tuple[FlaskClient, Customer, list],
    account_db_session: Session,
) -> None:
    """Existing pending request returns same row rather than creating a new one."""
    from sqlalchemy import func, select

    from review_app.refunds.reprints_models import ReprintRequest

    client, _cust, items = order_with_two_items
    payload = {
        "order_item_ids": [str(items[0].id)],
        "reason": "warped",
        "comment": "",
    }
    client.post(f"/account/orders/{items[0].order_id}/reprint", data=payload)
    client.post(f"/account/orders/{items[0].order_id}/reprint", data=payload)

    count = account_db_session.execute(
        select(func.count(ReprintRequest.id))
    ).scalar_one()
    assert count == 1
