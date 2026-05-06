"""Roundtrip + validation tests for review_app.prodigi.models."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from review_app.prodigi.models import (
    Address,
    CallbackPayload,
    Cost,
    Order,
    OrderRequest,
    OrderResponse,
    ProductDetails,
    QuoteResponse,
    Recipient,
)


class TestCost:
    def test_amount_cents_basic(self) -> None:
        c = Cost(amount="55.00", currency="USD")
        assert c.amount_cents() == 5500

    def test_amount_cents_decimal_precision(self) -> None:
        c = Cost(amount="12.345", currency="USD")
        # ROUND_HALF_UP: 1234.5 → 1235
        assert c.amount_cents() == 1235

    def test_amount_cents_zero(self) -> None:
        assert Cost(amount="0.00", currency="GBP").amount_cents() == 0


class TestOrderResponse:
    def test_parses_full_created_order(self, order_created_fixture: dict[str, Any]) -> None:
        envelope = OrderResponse.model_validate(order_created_fixture)
        assert envelope.outcome == "Created"
        assert envelope.order is not None
        order = envelope.order
        assert order.id == "ord_1234567"
        assert order.status.stage == "InProgress"
        assert order.shipping_method == "Standard"
        assert order.idempotency_key == "wildprint-test-001"
        assert order.recipient.address.country_code == "US"
        assert order.items[0].sku == "GLOBAL-CFPM-16X20"
        assert order.items[0].copies == 1
        assert order.items[0].attributes == {"color": "black"}
        rc = order.items[0].recipient_cost
        assert rc is not None and rc.amount == "55.00"

    def test_parses_complete_order_with_shipment(
        self, order_complete_fixture: dict[str, Any]
    ) -> None:
        envelope = OrderResponse.model_validate(order_complete_fixture)
        order = envelope.order
        assert order is not None
        assert order.status.stage == "Complete"
        assert len(order.shipments) == 1
        sh = order.shipments[0]
        assert sh.id == "shp_AAAA"
        assert sh.carrier is not None
        assert sh.carrier.name == "USPS"
        assert sh.tracking is not None
        assert sh.tracking.number == "9400111111111111111111"

    def test_unknown_top_level_fields_tolerated(
        self, order_created_fixture: dict[str, Any]
    ) -> None:
        # Forward-compatible: a future schema bump shouldn't crash us.
        order_created_fixture["futureField"] = {"foo": "bar"}
        envelope = OrderResponse.model_validate(order_created_fixture)
        assert envelope.outcome == "Created"


class TestQuoteResponse:
    def test_parses_quote(self, quote_fixture: dict[str, Any]) -> None:
        resp = QuoteResponse.model_validate(quote_fixture)
        assert resp.outcome == "Created"
        assert len(resp.quotes) == 2
        budget = resp.quotes[0]
        assert budget.shipment_method == "Budget"
        assert budget.cost_summary is not None
        tc = budget.cost_summary.total_cost
        assert tc is not None and tc.amount == "16.00"


class TestProductDetails:
    def test_parses_product(self, product_fixture: dict[str, Any]) -> None:
        details = ProductDetails.model_validate(product_fixture)
        assert details.outcome == "Ok"
        assert details.product is not None
        p = details.product
        assert p.sku == "GLOBAL-CFPM-16X20"
        assert p.attributes["color"] == [
            "black",
            "white",
            "natural",
            "antique-silver",
            "antique-gold",
            "brown",
            "dark-grey",
            "light-grey",
        ]
        assert "default" in p.print_areas


class TestCallbackPayload:
    def test_parses_inprogress_event(
        self, callback_inprogress_fixture: dict[str, Any]
    ) -> None:
        cb = CallbackPayload.model_validate(callback_inprogress_fixture)
        assert cb.id == "evt_abc123"
        assert cb.type == "com.prodigi.order.status.stage.changed#InProgress"
        assert cb.prodigi_order_id() == "ord_1234567"

    def test_parses_shipment_event(
        self, callback_shipment_fixture: dict[str, Any]
    ) -> None:
        cb = CallbackPayload.model_validate(callback_shipment_fixture)
        assert cb.prodigi_order_id() == "ord_2222222"


class TestValidation:
    def test_address_requires_postal_code(self) -> None:
        with pytest.raises(ValidationError):
            Address.model_validate(
                {
                    "line1": "1 Test",
                    "countryCode": "US",
                    "townOrCity": "Anywhere",
                }
            )

    def test_recipient_requires_address(self) -> None:
        with pytest.raises(ValidationError):
            Recipient.model_validate({"name": "No Address"})

    def test_order_request_serializes_camelcase(self) -> None:
        req = OrderRequest(
            shippingMethod="Standard",
            recipient=Recipient(
                name="Test",
                address=Address(
                    line1="1 Test St",
                    postalOrZipCode="12345",
                    countryCode="US",
                    townOrCity="Anywhere",
                ),
            ),
            items=[],
            merchantReference="mref",
            idempotencyKey="ikey",
        )
        dumped = req.model_dump(by_alias=True, exclude_none=True)
        assert dumped["shippingMethod"] == "Standard"
        assert dumped["recipient"]["address"]["postalOrZipCode"] == "12345"
        assert dumped["merchantReference"] == "mref"


class TestOrderRoundtrip:
    def test_order_dump_then_reload(self, order_created_fixture: dict[str, Any]) -> None:
        envelope = OrderResponse.model_validate(order_created_fixture)
        assert envelope.order is not None
        dumped = envelope.order.model_dump(mode="json", by_alias=True)
        reloaded = Order.model_validate(dumped)
        assert reloaded.id == envelope.order.id
        assert reloaded.status.stage == envelope.order.status.stage
        assert len(reloaded.items) == len(envelope.order.items)
