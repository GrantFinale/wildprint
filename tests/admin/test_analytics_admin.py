"""Phase 4b — Analytics admin route tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


def _seed_one_order(db_session: Session, *, total_cents: int = 7500) -> None:
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order, OrderItem
    from review_app.prodigi.db_models import ProdigiSku

    sku = ProdigiSku(
        internal_sku=f"SKU-{uuid.uuid4().hex[:6]}",
        prodigi_sku="GLOBAL-FAP-X",
        finish="Black",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=total_cents,
        in_stock=True,
    )
    db_session.add(sku)
    db_session.flush()

    cust = Customer.create(email=f"a{uuid.uuid4().hex[:6]}@x.com", name="x")
    db_session.add(cust)
    db_session.flush()
    addr = Address(
        customer_id=cust.id, line1="X", city="Y", state="CA",
        zip="12345", country="US",
    )
    db_session.add(addr)
    db_session.flush()
    o = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:12]}",
        status="paid",
        subtotal_cents=total_cents,
        total_cents=total_cents,
        currency="USD",
        source="web",
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
        shipped_at=datetime.now(UTC) + timedelta(hours=12),
    )
    db_session.add(o)
    db_session.flush()
    db_session.add(
        OrderItem(
            order_id=o.id,
            prodigi_sku_internal=sku.internal_sku,
            quantity=1,
            unit_price_cents=total_cents,
            line_total_cents=total_cents,
            finish_display="Black",
            size_inches="16x20",
        )
    )
    db_session.flush()


def test_sales_aggregates_orders_total(
    admin_client: FlaskClient, db_session: Session
) -> None:
    _seed_one_order(db_session, total_cents=7500)
    _seed_one_order(db_session, total_cents=2500)

    resp = admin_client.get("/admin/analytics/sales")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # $75 + $25 = $100 total.
    assert "100.00" in body
    # Orders count is 2.
    assert ">2<" in body or "2 " in body or '>2 ' in body
    # AOV $50.
    assert "50.00" in body


def test_ai_usage_groups_by_provider(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """Inserting AI usage rows surfaces them in the by-provider table."""
    from review_app.ai.models import AIUsageLog

    # Explicit ids — SQLite doesn't autoincrement BIGINT PKs the way it
    # does plain INTEGER PKs (the AIUsageLog model uses BigInteger without
    # the dialect-variant trick the outbox model uses).
    db_session.add(
        AIUsageLog(
            id=1,
            provider="openai",
            model="gpt-4o-mini",
            endpoint="chat.completions.create",
            cost_cents=120,
            status="ok",
            created_at=datetime.now(UTC),
        )
    )
    db_session.add(
        AIUsageLog(
            id=2,
            provider="recraft",
            model="recraft-v3",
            endpoint="images.generate",
            cost_cents=80,
            status="ok",
            created_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    resp = admin_client.get("/admin/analytics/ai-usage")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "openai" in body
    assert "recraft" in body
    # 120 + 80 = 200 cents = $2.00.
    assert "2.00" in body
    # Provider breakdown values.
    assert "1.20" in body or "1.2" in body  # openai cost
    assert "0.80" in body or "0.8" in body  # recraft cost


def test_operations_calcs_avg_time_to_production(
    admin_client: FlaskClient, db_session: Session
) -> None:
    _seed_one_order(db_session)
    _seed_one_order(db_session)

    resp = admin_client.get("/admin/analytics/operations")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Avg time to production" in body
    assert "Avg time to ship" in body
    # Both orders shipped 12h after creation.
    assert "12.0" in body or "12 " in body
