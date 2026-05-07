"""HTTP-level tests for /api/checkout/start.

The Stripe SDK is mocked via monkeypatching review_app.checkout.stripe_client.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


def test_checkout_start_rejects_unverified_address(
    checkout_client: FlaskClient,
    populated_db: dict[str, Any],
    checkout_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flip the address back to unverified.
    addr = populated_db["address"]
    addr.validated_at = None
    addr.dpv_match_code = "N"
    checkout_db_session.commit()

    resp = checkout_client.post(
        "/api/checkout/start",
        json={
            "cart_id": str(populated_db["cart"].id),
            "shipping_address_id": str(addr.id),
            "customer_email": "buyer@example.com",
        },
    )
    assert resp.status_code == 400
    assert "deliverable" in resp.get_json()["error"].lower()


def test_checkout_start_creates_stripe_session_for_valid_cart(
    checkout_client: FlaskClient,
    populated_db: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = {"id": "cs_test_123", "url": "https://stripe.test/cs/cs_test_123"}
    mock_create = MagicMock(return_value=fake_session)
    monkeypatch.setattr(
        "review_app.checkout.routes.stripe_client.create_checkout_session_for_cart",
        mock_create,
    )

    resp = checkout_client.post(
        "/api/checkout/start",
        json={
            "cart_id": str(populated_db["cart"].id),
            "shipping_address_id": str(populated_db["address"].id),
            "customer_email": "buyer@example.com",
            "marketing_opt_in": True,
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["url"] == fake_session["url"]
    assert body["checkout_session_id"] == "cs_test_123"
    mock_create.assert_called_once()


def test_checkout_start_returns_400_for_empty_cart(
    checkout_client: FlaskClient,
    populated_db: dict[str, Any],
    checkout_db_session: Session,
) -> None:
    # Empty the cart.
    from sqlalchemy import delete

    from review_app.cart.models import CartItem

    checkout_db_session.execute(delete(CartItem).where(CartItem.cart_id == populated_db["cart"].id))
    checkout_db_session.commit()

    resp = checkout_client.post(
        "/api/checkout/start",
        json={
            "cart_id": str(populated_db["cart"].id),
            "shipping_address_id": str(populated_db["address"].id),
            "customer_email": "buyer@example.com",
        },
    )
    assert resp.status_code == 400
    assert "empty" in resp.get_json()["error"].lower()


def test_checkout_start_returns_404_for_unknown_cart(
    checkout_client: FlaskClient,
    populated_db: dict[str, Any],
) -> None:
    resp = checkout_client.post(
        "/api/checkout/start",
        json={
            "cart_id": "00000000-0000-7000-8000-000000000000",
            "shipping_address_id": str(populated_db["address"].id),
            "customer_email": "buyer@example.com",
        },
    )
    assert resp.status_code == 404


def test_checkout_start_validates_email(
    checkout_client: FlaskClient,
    populated_db: dict[str, Any],
) -> None:
    resp = checkout_client.post(
        "/api/checkout/start",
        json={
            "cart_id": str(populated_db["cart"].id),
            "shipping_address_id": str(populated_db["address"].id),
            "customer_email": "not-an-email",
        },
    )
    assert resp.status_code == 400
