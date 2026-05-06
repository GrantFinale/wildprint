"""Sandbox integration tests — gated by --integration and PRODIGI_API_KEY_SANDBOX.

These tests hit the real ``api.sandbox.prodigi.com`` endpoint. Run with::

    PRODIGI_API_KEY_SANDBOX=test_xxx pytest tests/prodigi/ -v --integration

Each test cleans up after itself (cancel orders immediately) so no
artifacts accumulate in the sandbox account.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from review_app.prodigi.client import (
    ProdigiClientError,
    get_sandbox_client,
)
from review_app.prodigi.models import (
    Address,
    Asset,
    Item,
    OrderRequest,
    QuoteRequest,
    Recipient,
)


pytestmark = pytest.mark.integration


def _skip_if_no_sandbox_key() -> None:
    if not os.environ.get("PRODIGI_API_KEY_SANDBOX"):
        pytest.skip("PRODIGI_API_KEY_SANDBOX not set")


def _us_recipient() -> Recipient:
    return Recipient(
        name="Wildprint Test",
        email="test@example.com",
        phoneNumber=None,
        address=Address(
            line1="1600 Pennsylvania Ave NW",
            line2=None,
            postalOrZipCode="20500",
            countryCode="US",
            townOrCity="Washington",
            stateOrCounty="DC",
        ),
    )


def _placeholder_asset_url() -> str:
    return "https://placehold.co/4800x6000/png"


def test_sandbox_get_product_classic_frame_16x20_black() -> None:
    _skip_if_no_sandbox_key()
    with get_sandbox_client() as client:
        product = client.get_product("GLOBAL-CFPM-16X20")
        assert product.sku.upper() == "GLOBAL-CFPM-16X20"
        assert "default" in product.print_areas


def test_sandbox_quote_returns_pricing() -> None:
    _skip_if_no_sandbox_key()
    with get_sandbox_client() as client:
        request = QuoteRequest(
            destinationCountryCode="US",
            currencyCode="USD",
            shippingMethod="Budget",
            items=[
                Item(
                    sku="GLOBAL-CFPM-16X20",
                    copies=1,
                    attributes={"color": "black"},
                    assets=[Asset(printArea="default")],
                )
            ],
        )
        response = client.quote(request)
        # Per Prodigi docs, quote endpoint returns 'created' or 'createdWithIssues'.
        assert "Created" in str(response.outcome) or response.outcome == "Ok"
        assert len(response.quotes) >= 1


def test_sandbox_create_order_then_get_then_cancel() -> None:
    _skip_if_no_sandbox_key()
    idempotency = f"wildprint-test-{uuid.uuid4()}"
    request = OrderRequest(
        shippingMethod="Standard",
        recipient=_us_recipient(),
        items=[
            Item(
                merchantReference="lifecycle-test",
                sku="GLOBAL-CFPM-16X20",
                copies=1,
                sizing="fillPrintArea",
                attributes={"color": "black"},
                assets=[Asset(printArea="default", url=_placeholder_asset_url())],
            )
        ],
        merchantReference="lifecycle-test",
        idempotencyKey=idempotency,
    )

    with get_sandbox_client() as client:
        # Create
        order = client.create_order(request, idempotency_key=idempotency)
        assert order.id.startswith("ord_")

        # Get
        same = client.get_order(order.id)
        assert same.id == order.id

        # Cancel — immediate. Allow a small grace period: sandbox sometimes
        # rejects cancels for ~1s while the order finishes initializing.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                cancelled = client.cancel_order(order.id)
                assert cancelled.id == order.id
                # Stage may be "Cancelled" or still propagating; either way
                # the cancel call must have returned 200.
                return
            except ProdigiClientError as exc:
                last_exc = exc
                time.sleep(2)
        if last_exc is not None:
            raise last_exc
