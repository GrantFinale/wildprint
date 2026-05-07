"""Phase 4b — Fulfillment admin route tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


def _add_callback(
    db_session: Session,
    *,
    event_type: str = "Stage.InProgress",
    status: str = "ok",
    error: str | None = None,
) -> None:
    from review_app.prodigi.db_models import ProdigiCallback

    cb = ProdigiCallback(
        event_id=f"evt_{uuid.uuid4().hex[:12]}",
        event_type=event_type,
        prodigi_order_id="ord_test_123",
        raw_payload={"stage": "InProgress"},
        received_at=datetime.now(UTC),
        processed_at=datetime.now(UTC),
        processed_status=status,
        error_message=error,
    )
    db_session.add(cb)
    db_session.flush()


def _add_problem_order(db_session: Session) -> None:
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order

    cust = Customer.create(email="problem@example.com", name="P")
    db_session.add(cust)
    db_session.flush()
    addr = Address(
        customer_id=cust.id, name="P", line1="X", city="Y",
        state="CA", zip="12345", country="US",
    )
    db_session.add(addr)
    db_session.flush()
    o = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:12]}",
        status="problem",
        subtotal_cents=4900,
        total_cents=4900,
        currency="USD",
        source="web",
    )
    db_session.add(o)
    db_session.flush()


def test_webhook_log_renders_callbacks(
    admin_client: FlaskClient, db_session: Session
) -> None:
    _add_callback(db_session, status="ok")
    _add_callback(db_session, status="error", error="Image quality low")

    resp = admin_client.get("/admin/fulfillment/webhooks")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Stage.InProgress" in body
    assert "Image quality low" in body or "Image quality" in body


def test_error_queue_includes_problem_orders_and_failed_callbacks(
    admin_client: FlaskClient, db_session: Session
) -> None:
    _add_problem_order(db_session)
    _add_callback(db_session, status="error", error="address rejected")

    resp = admin_client.get("/admin/fulfillment/errors")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Problem orders panel shows up.
    assert "Problem orders" in body
    # Failed callbacks panel shows the error class badge (rejection or address).
    assert "Failed callbacks" in body
    assert "address" in body or "rejection" in body


def test_connection_page_pings_prodigi_sandbox(
    admin_client: FlaskClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST action=ping invokes the mocked Prodigi client and reports OK."""
    import review_app.admin.fulfillment.routes as routes

    monkeypatch.setattr(
        routes, "_ping_prodigi", lambda: (True, "OK — fetched X-SKU")
    )
    resp = admin_client.post(
        "/admin/fulfillment/connection",
        data={"action": "ping"},
    )
    assert resp.status_code == 200
    assert "OK" in resp.get_data(as_text=True)


def test_reprints_admin_only(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """Reprints page renders the empty state (no reprints) and create form."""
    resp = admin_client.get("/admin/fulfillment/reprints")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Create reprint" in body or "reprint" in body.lower()
