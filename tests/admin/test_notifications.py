"""Tests for /admin/notifications polling endpoint (Phase 6 polish)."""
from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from review_app.admin import notifications as notif_mod
from review_app.db import Base, get_engine, get_session


@pytest.fixture(autouse=True)
def _bootstrap_and_clear() -> None:
    """Bootstrap dev.db schema + drop the polling cache before/after each test."""
    Base.metadata.create_all(get_engine())
    notif_mod.reset_cache()
    yield
    notif_mod.reset_cache()


def _payload_from(client: FlaskClient) -> dict:
    resp = client.get("/admin/notifications")
    assert resp.status_code == 200
    return json.loads(resp.data)


def test_notifications_empty_when_nothing_pending(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Fresh DB -> count 0, no items."""
    role_setter("admin")
    payload = _payload_from(client)
    assert payload["count"] == 0
    assert payload["items"] == []


def test_notifications_picks_up_problem_orders(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Order with status='problem' surfaces an item."""
    from review_app.customers.models import Customer
    from review_app.orders.models import Order

    role_setter("admin")
    pi = "pi_notif_problem_xyz"
    cust_email = "notif-problem@example.com"
    with get_session() as session:
        cust = Customer.create(email=cust_email)
        session.add(cust)
        session.flush()
        session.add(
            Order(
                customer_id=cust.id,
                stripe_payment_intent_id=pi,
                status="problem",
                subtotal_cents=100,
                shipping_cents=0,
                tax_cents=0,
                total_cents=100,
            )
        )

    try:
        payload = _payload_from(client)
        assert payload["count"] >= 1
        types = [it["type"] for it in payload["items"]]
        assert "order.problem" in types
    finally:
        with get_session() as session:
            session.execute(
                Order.__table__.delete().where(Order.stripe_payment_intent_id == pi)
            )
            session.execute(
                Customer.__table__.delete().where(Customer.email == cust_email)
            )


def test_notifications_picks_up_callback_errors(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """A prodigi_callbacks row with processed_status='error' surfaces."""
    import uuid as _uuid

    from review_app.prodigi.db_models import ProdigiCallback

    role_setter("admin")
    ev_id = "ev_test_notif_" + _uuid.uuid4().hex[:8]
    with get_session() as session:
        session.add(
            ProdigiCallback(
                event_id=ev_id,
                event_type="order.update",
                prodigi_order_id="ord_test",
                raw_payload={"status": "Error"},
                processed_status="error",
            )
        )

    try:
        payload = _payload_from(client)
        types = [it["type"] for it in payload["items"]]
        assert "callback.error" in types
    finally:
        with get_session() as session:
            session.execute(
                ProdigiCallback.__table__.delete().where(
                    ProdigiCallback.event_id == ev_id
                )
            )


def test_notifications_picks_up_low_margin_skus(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """A prodigi_skus row with margin < 60% surfaces."""
    from review_app.prodigi.db_models import ProdigiSku

    role_setter("admin")
    sku_id = "TEST-LOW-MARGIN-SKU"
    with get_session() as session:
        existing = session.execute(
            select(ProdigiSku).where(ProdigiSku.internal_sku == sku_id)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                ProdigiSku(
                    internal_sku=sku_id,
                    prodigi_sku="GLOBAL-FAP-LOW",
                    finish="Black",
                    size_inches="16x20",
                    orientation="portrait",
                    active=True,
                    in_stock=True,
                    retail_price_cents=1000,
                    last_quoted_wholesale_cents=600,  # 40% margin
                )
            )

    try:
        payload = _payload_from(client)
        types = [it["type"] for it in payload["items"]]
        assert "sku.low_margin" in types
    finally:
        with get_session() as session:
            session.execute(
                ProdigiSku.__table__.delete().where(
                    ProdigiSku.internal_sku == sku_id
                )
            )


def test_notifications_caches_within_ttl(
    client: FlaskClient,
    role_setter: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two calls within 30s should not re-query the DB."""
    role_setter("admin")
    call_count = {"n": 0}

    def _spy() -> dict:
        call_count["n"] += 1
        return {"count": 0, "items": []}

    monkeypatch.setattr(notif_mod, "_build_payload", _spy)
    notif_mod.reset_cache()

    _payload_from(client)
    _payload_from(client)
    _payload_from(client)
    assert call_count["n"] == 1


def test_notifications_reset_cache_forces_refetch(
    client: FlaskClient,
    role_setter: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset_cache`` makes the next request rebuild the payload."""
    role_setter("admin")
    call_count = {"n": 0}

    def _spy() -> dict:
        call_count["n"] += 1
        return {"count": 0, "items": []}

    monkeypatch.setattr(notif_mod, "_build_payload", _spy)
    notif_mod.reset_cache()
    _payload_from(client)
    notif_mod.reset_cache()
    _payload_from(client)
    assert call_count["n"] == 2


def test_notifications_visible_to_viewer(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """All admin roles can poll notifications."""
    role_setter("viewer")
    resp = client.get("/admin/notifications")
    assert resp.status_code == 200


def test_notifications_returns_valid_json_shape(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Response shape is {count: int, items: list}."""
    role_setter("staff")
    payload = _payload_from(client)
    assert isinstance(payload["count"], int)
    assert isinstance(payload["items"], list)
    for it in payload["items"]:
        assert "type" in it and "message" in it and "link" in it and "created_at" in it
