"""Phase 4b — Customers admin route tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


def _seed_customer_with_orders(
    db_session: Session,
    *,
    email: str,
    order_totals_cents: list[int],
) -> uuid.UUID:
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order

    cust = Customer.create(email=email, name="Buyer")
    db_session.add(cust)
    db_session.flush()
    addr = Address(
        customer_id=cust.id,
        name="Buyer",
        line1="1 Pier",
        city="SF",
        state="CA",
        zip="94110",
        country="US",
    )
    db_session.add(addr)
    db_session.flush()
    for total in order_totals_cents:
        db_session.add(
            Order(
                customer_id=cust.id,
                shipping_address_id=addr.id,
                stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:12]}",
                status="paid",
                subtotal_cents=total,
                total_cents=total,
                currency="USD",
                source="web",
                placed_at=datetime.now(UTC),
                paid_at=datetime.now(UTC),
            )
        )
    db_session.flush()
    return cust.id


def test_customers_list_search_by_email(
    admin_client: FlaskClient, db_session: Session
) -> None:
    _seed_customer_with_orders(
        db_session, email="findme@example.com", order_totals_cents=[1000]
    )
    _seed_customer_with_orders(
        db_session, email="other@example.com", order_totals_cents=[1000]
    )

    resp = admin_client.get("/admin/customers?q=findme")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "findme@example.com" in body
    assert "other@example.com" not in body


def test_customer_detail_aggregates_orders_addresses_emails(
    admin_client: FlaskClient, db_session: Session
) -> None:
    cid = _seed_customer_with_orders(
        db_session,
        email="agg@example.com",
        order_totals_cents=[3000, 7000],
    )
    resp = admin_client.get(f"/admin/customers/{cid}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "agg@example.com" in body
    # Two orders aggregated into the section.
    assert 'data-section="orders"' in body
    # Addresses panel.
    assert "1 Pier" in body
    # LTV is sum of $30 + $70 = $100.
    assert "100.00" in body


def test_customer_ltv_calculation(db_session: Session) -> None:
    from review_app.admin.customers.routes import _ltv_by_customer

    cid = _seed_customer_with_orders(
        db_session,
        email="ltv@example.com",
        order_totals_cents=[2500, 5000, 1500],
    )
    out = _ltv_by_customer(db_session, [cid])
    assert cid in out
    order_count, ltv_cents, last_at = out[cid]
    assert order_count == 3
    assert ltv_cents == 9000
    assert last_at is not None
