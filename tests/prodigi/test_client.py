"""Unit tests for review_app.prodigi.client.ProdigiClient.

All HTTP I/O is mocked via httpx.MockTransport so no network access is
required. Each test owns its own client instance with a controlled
transport.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog

from review_app.prodigi.client import (
    ProdigiClient,
    ProdigiClientError,
    _compute_backoff,
    get_default_client,
    get_live_client,
    get_sandbox_client,
)
from review_app.prodigi.models import (
    Address,
    Item,
    OrderRequest,
    QuoteRequest,
    Recipient,
)


def _build_client(handler: Any, *, max_attempts: int = 5) -> ProdigiClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://api.sandbox.prodigi.com",
        transport=transport,
        timeout=10.0,
        headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
    )
    return ProdigiClient(
        api_key="test-key",
        base_url="https://api.sandbox.prodigi.com",
        client=http_client,
        max_attempts=max_attempts,
        retry_base_seconds=0.001,  # don't actually sleep in tests
    )


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode(),
    )


# ---------------------------------------------------------------------------
# Backoff math
# ---------------------------------------------------------------------------
class TestBackoff:
    def test_grows_exponentially(self) -> None:
        b1 = _compute_backoff(1, base=1.0, jitter=False)
        b2 = _compute_backoff(2, base=1.0, jitter=False)
        b3 = _compute_backoff(3, base=1.0, jitter=False)
        assert b1 == 1.0
        assert b2 == 2.0
        assert b3 == 4.0

    def test_jitter_stays_in_band(self) -> None:
        for _ in range(20):
            v = _compute_backoff(1, base=1.0, jitter=True)
            assert 0.5 <= v <= 1.5


# ---------------------------------------------------------------------------
# get_product
# ---------------------------------------------------------------------------
class TestGetProduct:
    def test_get_product_returns_typed_object(
        self, product_fixture: dict[str, Any]
    ) -> None:
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["method"] = req.method
            captured["x_api_key"] = req.headers.get("X-API-Key")
            return _json_response(product_fixture)

        client = _build_client(handler)
        product = client.get_product("GLOBAL-CFPM-16X20")
        assert product.sku == "GLOBAL-CFPM-16X20"
        assert "color" in product.attributes
        assert captured["method"] == "GET"
        assert captured["url"].endswith("/v4.0/products/GLOBAL-CFPM-16X20")
        assert captured["x_api_key"] == "test-key"

    def test_get_product_404_raises_immediately(self) -> None:
        attempts: list[int] = []

        def handler(req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(404, json={"outcome": "ProductDoesNotExist"})

        client = _build_client(handler)
        with pytest.raises(ProdigiClientError) as exc_info:
            client.get_product("BAD-SKU")
        assert exc_info.value.status_code == 404
        assert exc_info.value.outcome == "ProductDoesNotExist"
        # 4xx must not retry
        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# create_order
# ---------------------------------------------------------------------------
def _sample_order_request() -> OrderRequest:
    return OrderRequest(
        shippingMethod="Standard",
        recipient=Recipient(
            name="Test",
            address=Address(
                line1="1 Test St",
                postalOrZipCode="12345",
                countryCode="US",
                townOrCity="Testville",
                stateOrCounty="TS",
            ),
        ),
        items=[
            Item(
                sku="GLOBAL-CFPM-16X20",
                copies=1,
                attributes={"color": "black"},
                assets=[],
            ),
        ],
    )


class TestCreateOrder:
    def test_create_order_with_idempotency_key(
        self, order_created_fixture: dict[str, Any]
    ) -> None:
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["idem"] = req.headers.get("Idempotency-Key")
            captured["body"] = json.loads(req.content.decode())
            return _json_response(order_created_fixture)

        client = _build_client(handler)
        order = client.create_order(
            _sample_order_request(),
            idempotency_key="wildprint-test-001",
        )
        assert order.id == "ord_1234567"
        assert captured["idem"] == "wildprint-test-001"
        assert captured["body"]["shippingMethod"] == "Standard"
        assert captured["url"].endswith("/v4.0/Orders")


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------
class TestRetry:
    def test_retry_on_500_succeeds_on_third_attempt(
        self, order_created_fixture: dict[str, Any]
    ) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, json={"outcome": "TempError"})
            return _json_response(order_created_fixture)

        client = _build_client(handler)
        order = client.get_order("ord_1234567")
        assert order.id == "ord_1234567"
        assert calls["n"] == 3

    def test_no_retry_on_400_immediate_error(self) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, json={"outcome": "InsufficientData"})

        client = _build_client(handler)
        with pytest.raises(ProdigiClientError) as exc_info:
            client.get_order("ord_anything")
        assert exc_info.value.status_code == 400
        assert calls["n"] == 1

    def test_429_respects_retry_after_header_if_present(
        self, order_created_fixture: dict[str, Any]
    ) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429,
                    json={"outcome": "RateLimited"},
                    headers={"Retry-After": "0.001"},
                )
            return _json_response(order_created_fixture)

        client = _build_client(handler)
        order = client.get_order("ord_1234567")
        assert order.id == "ord_1234567"
        assert calls["n"] == 2

    def test_retry_exhaustion_raises(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"outcome": "TempError"})

        client = _build_client(handler, max_attempts=3)
        with pytest.raises(ProdigiClientError) as exc_info:
            client.get_order("ord_anything")
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class TestLogging:
    def test_logs_request_id_endpoint_status_latency(
        self,
        product_fixture: dict[str, Any],
    ) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return _json_response(product_fixture)

        client = _build_client(handler)
        with structlog.testing.capture_logs() as captured:
            client.get_product("GLOBAL-CFPM-16X20")

        events = [e for e in captured if e.get("event") == "prodigi_request_ok"]
        assert events, f"no prodigi_request_ok event captured; got {captured}"
        evt = events[0]
        for key in ("request_id", "endpoint", "status", "latency_ms"):
            assert key in evt, f"expected {key!r} in {evt!r}"
        assert evt["status"] == 200
        assert evt["endpoint"] == "/v4.0/products/GLOBAL-CFPM-16X20"
        assert isinstance(evt["latency_ms"], int)


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------
class TestQuote:
    def test_quote_parses(self, quote_fixture: dict[str, Any]) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return _json_response(quote_fixture)

        client = _build_client(handler)
        request = QuoteRequest(
            destinationCountryCode="US",
            currencyCode="USD",
            items=[
                Item(
                    sku="GLOBAL-CFPM-12X16",
                    copies=1,
                    attributes={"color": "black"},
                    assets=[],
                )
            ],
        )
        response = client.quote(request)
        assert response.outcome == "Created"
        assert len(response.quotes) == 2


# ---------------------------------------------------------------------------
# Cancel + update
# ---------------------------------------------------------------------------
class TestActionEndpoints:
    def test_cancel(self, order_complete_fixture: dict[str, Any]) -> None:
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            return _json_response({"outcome": "Cancelled", "order": order_complete_fixture["order"]})

        client = _build_client(handler)
        order = client.cancel_order("ord_2222222")
        assert order.id == "ord_2222222"
        assert captured["url"].endswith("/v4.0/Orders/ord_2222222/actions/cancel")

    def test_update_shipping_method(self, order_complete_fixture: dict[str, Any]) -> None:
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(req.content.decode())
            return _json_response({"outcome": "Ok", "order": order_complete_fixture["order"]})

        client = _build_client(handler)
        client.update_shipping_method("ord_X", "Express")
        assert captured["body"] == {"shippingMethod": "Express"}


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
class TestFactories:
    def test_get_sandbox_client_requires_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRODIGI_API_KEY_SANDBOX", raising=False)
        with pytest.raises(RuntimeError, match="PRODIGI_API_KEY_SANDBOX"):
            get_sandbox_client()

    def test_get_live_client_requires_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRODIGI_API_KEY_LIVE", raising=False)
        with pytest.raises(RuntimeError, match="PRODIGI_API_KEY_LIVE"):
            get_live_client()

    def test_get_default_client_picks_sandbox_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRODIGI_API_KEY_SANDBOX", "x")
        monkeypatch.setenv("PRODIGI_API_BASE_SANDBOX", "https://api.sandbox.prodigi.com")
        monkeypatch.delenv("PRODIGI_ENV", raising=False)
        c = get_default_client()
        try:
            assert c._base_url == "https://api.sandbox.prodigi.com"  # noqa: SLF001
        finally:
            c.close()

    def test_get_default_client_live_when_env_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRODIGI_ENV", "live")
        monkeypatch.setenv("PRODIGI_API_KEY_LIVE", "x")
        monkeypatch.setenv("PRODIGI_API_BASE_LIVE", "https://api.prodigi.com")
        c = get_default_client()
        try:
            assert c._base_url == "https://api.prodigi.com"  # noqa: SLF001
        finally:
            c.close()


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------
class TestNetworkErrors:
    def test_transport_error_retried_then_fails(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated DNS failure")

        client = _build_client(handler, max_attempts=2)
        with pytest.raises(ProdigiClientError, match="2 attempts"):
            client.get_order("ord_x")
