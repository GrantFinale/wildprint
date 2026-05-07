"""Tests for the v2 Stripe webhook handler.

Signature verification is mocked at the stripe_client.construct_event_from_payload
boundary so tests don't need real Stripe secrets or signed payloads.
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Helper to drive the webhook with a fake-verified event.
# ---------------------------------------------------------------------------
def _post_event(
    client: "FlaskClient",
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, Any],
) -> Any:
    monkeypatch.setattr(
        "review_app.checkout.webhook.stripe_client.construct_event_from_payload",
        MagicMock(return_value=event),
    )
    return client.post(
        "/webhook/stripe/v2",
        data=json.dumps(event),
        content_type="application/json",
        headers={"Stripe-Signature": "t=1,v1=fakesig"},
    )


def _checkout_session_event(
    *, event_id: str, cart_id: str, address_id: str, payment_intent_id: str = "pi_test_123",
    email: str = "buyer@example.com",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_xxx",
                "payment_intent": payment_intent_id,
                "customer_email": email,
                "metadata": {
                    "cart_id": cart_id,
                    "shipping_address_id": address_id,
                    "customer_email_lower": email,
                    "wildprint_flow": "physical_v1",
                },
                "shipping_cost": {"amount_total": 0},
                "total_details": {"amount_tax": 0},
            }
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_webhook_signature_verified_and_event_persisted(
    checkout_client: "FlaskClient",
    populated_db: dict[str, Any],
    checkout_db_session: "Session",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from review_app.checkout.stripe_events import StripeEvent
    from sqlalchemy import select

    event = _checkout_session_event(
        event_id="evt_001",
        cart_id=str(populated_db["cart"].id),
        address_id=str(populated_db["address"].id),
    )
    resp = _post_event(checkout_client, monkeypatch, event)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    checkout_db_session.expire_all()
    found = checkout_db_session.execute(
        select(StripeEvent).where(StripeEvent.event_id == "evt_001")
    ).scalar_one_or_none()
    assert found is not None
    assert found.event_type == "checkout.session.completed"
    assert found.processed_status == "ok"


def test_webhook_dedupe_by_event_id(
    checkout_client: "FlaskClient",
    populated_db: dict[str, Any],
    checkout_db_session: "Session",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from review_app.checkout.stripe_events import StripeEvent
    from sqlalchemy import func, select

    event = _checkout_session_event(
        event_id="evt_dup",
        cart_id=str(populated_db["cart"].id),
        address_id=str(populated_db["address"].id),
    )
    r1 = _post_event(checkout_client, monkeypatch, event)
    r2 = _post_event(checkout_client, monkeypatch, event)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "duplicate"

    checkout_db_session.expire_all()
    count = checkout_db_session.execute(
        select(func.count()).select_from(StripeEvent).where(
            StripeEvent.event_id == "evt_dup"
        )
    ).scalar_one()
    assert count == 1


def test_webhook_session_completed_persists_order_and_outbox_rows(
    checkout_client: "FlaskClient",
    populated_db: dict[str, Any],
    checkout_db_session: "Session",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from review_app.email.outbox import OutboxEntry
    from review_app.orders.models import Order

    event = _checkout_session_event(
        event_id="evt_002",
        cart_id=str(populated_db["cart"].id),
        address_id=str(populated_db["address"].id),
        payment_intent_id="pi_complete_001",
    )
    resp = _post_event(checkout_client, monkeypatch, event)
    assert resp.status_code == 200

    checkout_db_session.expire_all()
    order = checkout_db_session.execute(
        select(Order).where(Order.stripe_payment_intent_id == "pi_complete_001")
    ).scalar_one()
    assert order.status == "paid"
    assert order.total_cents == 12900

    kinds = [
        row.kind
        for row in checkout_db_session.execute(select(OutboxEntry)).scalars().all()
    ]
    assert "prodigi.create_order" in kinds
    assert "render.tier_3" in kinds
    assert "email.order_confirmed" in kinds


def test_webhook_payment_intent_succeeded_marks_order_paid(
    checkout_client: "FlaskClient",
    populated_db: dict[str, Any],
    checkout_db_session: "Session",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from review_app.orders.models import Order

    # First land checkout.session.completed to create the order.
    cs_event = _checkout_session_event(
        event_id="evt_pi_001",
        cart_id=str(populated_db["cart"].id),
        address_id=str(populated_db["address"].id),
        payment_intent_id="pi_paid_001",
    )
    _post_event(checkout_client, monkeypatch, cs_event)

    # Now flip the order back to pending so we can prove succeeded re-marks it.
    checkout_db_session.expire_all()
    order = checkout_db_session.execute(
        select(Order).where(Order.stripe_payment_intent_id == "pi_paid_001")
    ).scalar_one()
    order.status = "pending"
    order.paid_at = None
    checkout_db_session.commit()

    pi_event: dict[str, Any] = {
        "id": "evt_pi_002",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_paid_001",
                "receipt_email": "buyer@example.com",
            }
        },
    }
    resp = _post_event(checkout_client, monkeypatch, pi_event)
    assert resp.status_code == 200

    checkout_db_session.expire_all()
    order = checkout_db_session.execute(
        select(Order).where(Order.stripe_payment_intent_id == "pi_paid_001")
    ).scalar_one()
    assert order.status == "paid"
    assert order.paid_at is not None


def test_webhook_payment_intent_failed_marks_order_cancelled(
    checkout_client: "FlaskClient",
    populated_db: dict[str, Any],
    checkout_db_session: "Session",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from review_app.orders.models import Order

    # Create the order first.
    cs_event = _checkout_session_event(
        event_id="evt_fail_001",
        cart_id=str(populated_db["cart"].id),
        address_id=str(populated_db["address"].id),
        payment_intent_id="pi_fail_001",
    )
    _post_event(checkout_client, monkeypatch, cs_event)

    pi_event: dict[str, Any] = {
        "id": "evt_fail_002",
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": "pi_fail_001",
                "last_payment_error": {"message": "Card declined"},
                "receipt_email": "buyer@example.com",
            }
        },
    }
    resp = _post_event(checkout_client, monkeypatch, pi_event)
    assert resp.status_code == 200

    checkout_db_session.expire_all()
    order = checkout_db_session.execute(
        select(Order).where(Order.stripe_payment_intent_id == "pi_fail_001")
    ).scalar_one()
    assert order.status == "cancelled"


def test_webhook_invalid_signature_returns_400(
    checkout_client: "FlaskClient",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from review_app.checkout import stripe_client as sc

    monkeypatch.setattr(
        "review_app.checkout.webhook.stripe_client.construct_event_from_payload",
        MagicMock(side_effect=sc.StripeSignatureError("bad sig")),
    )
    resp = checkout_client.post(
        "/webhook/stripe/v2",
        data=b"{}",
        content_type="application/json",
        headers={"Stripe-Signature": "garbage"},
    )
    assert resp.status_code == 400
