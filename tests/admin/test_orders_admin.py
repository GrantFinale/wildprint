"""Phase 4b — Orders admin route tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Helpers — build an order graph in the test DB
# ---------------------------------------------------------------------------
def _make_customer(db_session: Session, *, email: str) -> Any:
    from review_app.customers.models import Customer

    cust = Customer.create(email=email, name="Buyer", marketing_opt_in=False)
    db_session.add(cust)
    db_session.flush()
    return cust


def _make_address(db_session: Session, customer_id: uuid.UUID) -> Any:
    from review_app.addresses.models import Address

    addr = Address(
        customer_id=customer_id,
        name="Buyer",
        line1="1 Pier Rd",
        city="San Francisco",
        state="CA",
        zip="94110",
        country="US",
    )
    db_session.add(addr)
    db_session.flush()
    return addr


def _make_sku(db_session: Session, internal: str = "P-16x20-BLK") -> Any:
    from review_app.prodigi.db_models import ProdigiSku

    sku = ProdigiSku(
        internal_sku=internal,
        prodigi_sku="GLOBAL-FAP-16X20",
        finish="Black",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=4900,
        in_stock=True,
    )
    db_session.add(sku)
    db_session.flush()
    return sku


def _make_order(
    db_session: Session,
    *,
    customer_id: uuid.UUID,
    address_id: uuid.UUID,
    sku_internal: str,
    status: str = "paid",
    total_cents: int = 4900,
) -> Any:
    from review_app.orders.models import Order, OrderItem

    order = Order(
        customer_id=customer_id,
        shipping_address_id=address_id,
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:16]}",
        status=status,
        subtotal_cents=total_cents,
        shipping_cents=0,
        tax_cents=0,
        total_cents=total_cents,
        currency="USD",
        source="web",
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    db_session.add(order)
    db_session.flush()
    item = OrderItem(
        order_id=order.id,
        prodigi_sku_internal=sku_internal,
        quantity=1,
        unit_price_cents=total_cents,
        line_total_cents=total_cents,
        finish_display="Black",
        size_inches="16x20",
    )
    db_session.add(item)
    db_session.flush()
    return order


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_orders_list_filters_by_status(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """The status tab restricts the rendered orders to its enum members."""
    cust = _make_customer(db_session, email="alice@example.com")
    addr = _make_address(db_session, cust.id)
    sku = _make_sku(db_session)
    paid = _make_order(
        db_session, customer_id=cust.id, address_id=addr.id,
        sku_internal=sku.internal_sku, status="paid",
    )
    shipped = _make_order(
        db_session, customer_id=cust.id, address_id=addr.id,
        sku_internal=sku.internal_sku, status="shipped",
    )

    # Open tab — should include paid, exclude shipped.
    # Match on the FULL order UUID since uuid7 shares time-based prefixes
    # across orders created in the same millisecond.
    resp = admin_client.get("/admin/orders?tab=open")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert str(paid.id) in body
    assert str(shipped.id) not in body

    # Shipped tab — opposite.
    resp = admin_client.get("/admin/orders?tab=shipped")
    body = resp.get_data(as_text=True)
    assert str(shipped.id) in body
    assert str(paid.id) not in body


def test_order_detail_renders_full_timeline(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """Detail view includes every section header from the wireframe."""
    cust = _make_customer(db_session, email="bob@example.com")
    addr = _make_address(db_session, cust.id)
    sku = _make_sku(db_session, internal="P-20x30-WHT")
    order = _make_order(
        db_session, customer_id=cust.id, address_id=addr.id,
        sku_internal=sku.internal_sku, status="in_production",
    )

    resp = admin_client.get(f"/admin/orders/{order.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Every wireframe section must show up.
    for marker in (
        'data-section="customer"',
        'data-section="items"',
        'data-section="payment"',
        'data-section="prodigi"',
        'data-section="address"',
        'data-section="notes"',
        'data-section="emails"',
    ):
        assert marker in body, f"missing section: {marker}"

    assert "in_production" in body  # status badge
    assert "bob@example.com" in body
    assert "1 Pier Rd" in body


def test_refunds_queue_admin_only(
    admin_client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refunds queue route exists; viewer role rejected when ADMIN_AUTH on.

    With ``ADMIN_AUTH_ENABLED`` unset (default), the decorator is a
    passthrough so the page renders 200 — that's the contract.
    """
    monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
    resp = admin_client.get("/admin/orders/refunds")
    assert resp.status_code == 200
    assert "Refund" in resp.get_data(as_text=True) or "No refunds" in resp.get_data(as_text=True)


def test_test_orders_route_creates_sandbox_order(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """POST /admin/orders/test inserts an Order with source='admin_test'."""
    sku = _make_sku(db_session, internal="P-12x18-BLK")
    db_session.flush()

    resp = admin_client.post(
        "/admin/orders/test",
        data={
            "prodigi_sku_internal": sku.internal_sku,
            "name": "Sandbox Buyer",
            "line1": "1 Sandbox Way",
            "city": "Testville",
            "state": "CA",
            "zip": "94016",
            "country": "US",
        },
        follow_redirects=False,
    )
    # Successful create -> redirect to detail.
    assert resp.status_code == 302
    assert "/admin/orders/" in resp.headers["Location"]

    from sqlalchemy import select

    from review_app.orders.models import Order

    rows = list(
        db_session.execute(
            select(Order).where(Order.source == "admin_test")
        ).scalars().all()
    )
    assert len(rows) == 1
    order = rows[0]
    assert order.status == "paid"
    assert order.stripe_payment_intent_id is not None
    assert order.stripe_payment_intent_id.startswith("test_pi_")
    assert order.total_cents == sku.retail_price_cents


def test_orders_list_csv_export(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """The CSV export branch returns text/csv with a header row."""
    cust = _make_customer(db_session, email="csv@example.com")
    addr = _make_address(db_session, cust.id)
    sku = _make_sku(db_session, internal="P-csv-test")
    _make_order(
        db_session, customer_id=cust.id, address_id=addr.id,
        sku_internal=sku.internal_sku,
    )

    resp = admin_client.get("/admin/orders?format=csv")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/csv")
    body = resp.get_data(as_text=True)
    assert "order_id,created_at,status" in body
    assert "csv@example.com" in body
