"""Typed HTTP client for the Prodigi v4.0 print API.

Design constraints (from `docs/integration-plan.md` and the Phase 1 spec):

* httpx — not requests. Modern, async-ready, type-friendly.
* Lazy initialization — importing this module never raises, even with no env.
* Retry with exponential backoff on 5xx, 429, and network errors. 4xx
  responses surface immediately as :class:`ProdigiClientError` so callers
  can react to validation problems without sleeping through retry windows.
* Idempotency — :meth:`ProdigiClient.create_order` accepts an
  ``idempotency_key`` argument that is sent via the ``Idempotency-Key``
  HTTP header (Prodigi's documented mechanism).
* Structured logging — every request logs ``request_id``, ``endpoint``,
  ``status``, ``latency_ms``, ``prodigi_outcome`` (when available) using
  ``structlog`` under the logger name ``prodigi.client``.
* Two factory helpers (sandbox / live) plus :func:`get_default_client`
  that selects based on the ``PRODIGI_ENV`` env var.

The client does NOT route through the AI usage logging interceptor — that
table is for OpenAI/Recraft/Replicate calls only — but mirrors the same
``structlog`` logger naming convention so log output is consistent.
"""
from __future__ import annotations

import os
import random
import time
import uuid
from typing import Any

import httpx

from review_app.observability import get_logger
from review_app.prodigi.models import (
    Address,
    Order,
    OrderActionResponse,
    OrderRequest,
    OrderResponse,
    Product,
    ProductDetails,
    QuoteRequest,
    QuoteResponse,
)

_log = get_logger("prodigi.client")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ProdigiClientError(Exception):
    """Raised on non-retryable Prodigi API errors (4xx, parse failures).

    Attributes:
        status_code: the HTTP status code if the failure was an HTTP response,
            else None.
        body: the raw response body (truncated to 4 KB) if available.
        outcome: the Prodigi ``outcome`` string if it could be extracted.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.outcome = outcome


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------
# Defaults: 1s, 2s, 4s, 8s, 16s — five attempts total (initial + 4 retries).
_DEFAULT_RETRY_BASE_SECONDS: float = 1.0
_DEFAULT_MAX_ATTEMPTS: int = 5
_DEFAULT_RETRY_STATUS_CODES: frozenset[int] = frozenset(
    {408, 425, 429, 500, 502, 503, 504}
)


def _compute_backoff(
    attempt: int,
    base: float = _DEFAULT_RETRY_BASE_SECONDS,
    *,
    jitter: bool = True,
) -> float:
    """Return the sleep duration before retry attempt ``attempt`` (1-indexed).

    attempt=1 -> ~1s, attempt=2 -> ~2s, etc. With jitter the value is uniformly
    sampled in [0.5x, 1.5x] of the base so a thundering herd of retries
    smears out across the wall clock.
    """
    delay: float = base * float(2 ** (attempt - 1))
    if jitter:
        delay *= 0.5 + random.random()
    return delay


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class ProdigiClient:
    """Synchronous Prodigi v4.0 client.

    Construction does not perform any network I/O; the underlying httpx
    client is created lazily on first use and reused for the lifetime of
    the instance. Call :meth:`close` (or use as a context manager) to
    release sockets.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        retry_base_seconds: float = _DEFAULT_RETRY_BASE_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ProdigiClient requires a non-empty api_key")
        if not base_url:
            raise ValueError("ProdigiClient requires a non-empty base_url")
        self._api_key = api_key
        # Always strip trailing slash so we can join paths predictably.
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._client: httpx.Client | None = client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        """Lazy httpx client construction."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                headers={
                    "X-API-Key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "wildprint-prodigi-client/1.0",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> ProdigiClient:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Core request loop
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Issue a request with retry/logging. Returns parsed JSON dict.

        Raises :class:`ProdigiClientError` for terminal failures.
        """
        request_id = str(uuid.uuid4())
        endpoint = path
        attempts = 0
        last_exc: Exception | None = None

        while attempts < self._max_attempts:
            attempts += 1
            started = time.monotonic()
            try:
                resp = self._http().request(
                    method=method,
                    url=path,
                    json=json,
                    headers=extra_headers,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                _log.warning(
                    "prodigi_request_network_error",
                    request_id=request_id,
                    endpoint=endpoint,
                    method=method,
                    attempt=attempts,
                    error=type(exc).__name__,
                    latency_ms=latency_ms,
                )
                last_exc = exc
                if attempts >= self._max_attempts:
                    raise ProdigiClientError(
                        f"Prodigi request failed after {attempts} attempts: {exc!r}",
                    ) from exc
                self._sleep_backoff(attempts, retry_after=None)
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            status = resp.status_code
            outcome = _extract_outcome(resp)

            log_kwargs: dict[str, Any] = {
                "request_id": request_id,
                "endpoint": endpoint,
                "method": method,
                "status": status,
                "latency_ms": latency_ms,
                "attempt": attempts,
                "prodigi_outcome": outcome,
            }

            # Retry on 5xx + 429 (and a few other transient codes).
            if status in _DEFAULT_RETRY_STATUS_CODES:
                _log.warning("prodigi_request_retryable", **log_kwargs)
                if attempts >= self._max_attempts:
                    raise ProdigiClientError(
                        f"Prodigi {method} {path} failed with HTTP {status}"
                        f" after {attempts} attempts",
                        status_code=status,
                        body=_truncate(resp.text),
                        outcome=outcome,
                    )
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                self._sleep_backoff(attempts, retry_after=retry_after)
                continue

            # 4xx (excluding the retryable codes above): surface immediately.
            if 400 <= status < 500:
                _log.error("prodigi_request_client_error", **log_kwargs)
                raise ProdigiClientError(
                    f"Prodigi {method} {path} returned HTTP {status}",
                    status_code=status,
                    body=_truncate(resp.text),
                    outcome=outcome,
                )

            # 2xx
            if 200 <= status < 300:
                _log.info("prodigi_request_ok", **log_kwargs)
                return _parse_json(resp)

            # 3xx or anything weird — surface as error.
            _log.error("prodigi_request_unexpected_status", **log_kwargs)
            raise ProdigiClientError(
                f"Prodigi {method} {path} returned unexpected HTTP {status}",
                status_code=status,
                body=_truncate(resp.text),
                outcome=outcome,
            )

        # Shouldn't reach here, but keep mypy happy.
        raise ProdigiClientError(
            f"Prodigi request gave up after {attempts} attempts (last_exc={last_exc!r})"
        )

    def _sleep_backoff(self, attempt: int, retry_after: float | None) -> None:
        """Sleep before the next retry. Honours ``Retry-After`` if provided."""
        if retry_after is not None and retry_after > 0:
            time.sleep(min(retry_after, 60.0))
            return
        time.sleep(_compute_backoff(attempt, base=self._retry_base_seconds))

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------
    def get_product(self, sku: str) -> Product:
        """GET /v4.0/products/{sku} — return the typed Product.

        Raises :class:`ProdigiClientError` on 4xx (e.g. 404 unknown SKU).
        """
        if not sku:
            raise ValueError("sku is required")
        body = self._request("GET", f"/v4.0/products/{sku}")
        details = ProductDetails.model_validate(body)
        if details.product is None:
            raise ProdigiClientError(
                f"Prodigi /products/{sku} returned no product object",
                outcome=str(details.outcome),
            )
        return details.product

    def create_order(
        self,
        request: OrderRequest,
        *,
        idempotency_key: str | None = None,
    ) -> Order:
        """POST /v4.0/Orders — create an order.

        ``idempotency_key`` is sent via the ``Idempotency-Key`` HTTP header.
        Per Prodigi docs, repeating the same key returns the existing order
        with outcome ``alreadyExists`` rather than creating a new one.
        """
        headers: dict[str, str] | None = None
        if idempotency_key:
            headers = {"Idempotency-Key": idempotency_key}
        body = self._request(
            "POST",
            "/v4.0/Orders",
            json=_dump_request(request),
            extra_headers=headers,
        )
        envelope = OrderResponse.model_validate(body)
        if envelope.order is None:
            raise ProdigiClientError(
                "Prodigi POST /Orders returned no order object",
                outcome=str(envelope.outcome),
            )
        return envelope.order

    def get_order(self, prodigi_order_id: str) -> Order:
        """GET /v4.0/Orders/{id}."""
        if not prodigi_order_id:
            raise ValueError("prodigi_order_id is required")
        body = self._request("GET", f"/v4.0/Orders/{prodigi_order_id}")
        envelope = OrderResponse.model_validate(body)
        if envelope.order is None:
            raise ProdigiClientError(
                f"Prodigi GET /Orders/{prodigi_order_id} returned no order",
                outcome=str(envelope.outcome),
            )
        return envelope.order

    def cancel_order(self, prodigi_order_id: str) -> Order:
        """POST /v4.0/Orders/{id}/actions/cancel."""
        body = self._request(
            "POST",
            f"/v4.0/Orders/{prodigi_order_id}/actions/cancel",
        )
        envelope = OrderActionResponse.model_validate(body)
        if envelope.order is None:
            raise ProdigiClientError(
                f"Prodigi cancel for {prodigi_order_id} returned no order",
                outcome=str(envelope.outcome),
            )
        return envelope.order

    def update_shipping_method(
        self, prodigi_order_id: str, shipping_method: str
    ) -> Order:
        """POST /v4.0/Orders/{id}/actions/updateShipping.

        Accepts one of ``Budget`` | ``Standard`` | ``StandardPlus`` |
        ``Express`` | ``Overnight``.
        """
        body = self._request(
            "POST",
            f"/v4.0/Orders/{prodigi_order_id}/actions/updateShipping",
            json={"shippingMethod": shipping_method},
        )
        envelope = OrderActionResponse.model_validate(body)
        if envelope.order is None:
            raise ProdigiClientError(
                f"updateShipping for {prodigi_order_id} returned no order",
                outcome=str(envelope.outcome),
            )
        return envelope.order

    def update_shipping_address(
        self, prodigi_order_id: str, recipient: dict[str, Any] | Address
    ) -> Order:
        """POST /v4.0/Orders/{id}/actions/updateRecipient.

        Pass either a Recipient-shaped dict or an :class:`Address` (we'll wrap
        the address in the recipient envelope).
        """
        if isinstance(recipient, Address):
            recipient_payload: dict[str, Any] = {
                "address": recipient.model_dump(by_alias=True, exclude_none=True),
            }
        else:
            recipient_payload = recipient
        body = self._request(
            "POST",
            f"/v4.0/Orders/{prodigi_order_id}/actions/updateRecipient",
            json={"recipient": recipient_payload},
        )
        envelope = OrderActionResponse.model_validate(body)
        if envelope.order is None:
            raise ProdigiClientError(
                f"updateRecipient for {prodigi_order_id} returned no order",
                outcome=str(envelope.outcome),
            )
        return envelope.order

    def get_order_actions(self, prodigi_order_id: str) -> dict[str, Any]:
        """GET /v4.0/Orders/{id}/actions — which actions are currently valid."""
        return self._request(
            "GET",
            f"/v4.0/Orders/{prodigi_order_id}/actions",
        )

    def quote(self, request: QuoteRequest) -> QuoteResponse:
        """POST /v4.0/Quotes — return all quotes (one per shipping method) ."""
        body = self._request(
            "POST",
            "/v4.0/Quotes",
            json=_dump_request(request),
        )
        return QuoteResponse.model_validate(body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dump_request(model: Any) -> dict[str, Any]:
    """Serialize a Pydantic request model to a JSON-ready dict.

    Uses ``by_alias=True`` so camelCase field names hit the wire, and
    ``exclude_none=True`` so we don't trip Prodigi's stricter required-field
    validation on optional null values.
    """
    return model.model_dump(by_alias=True, exclude_none=True)  # type: ignore[no-any-return]


def _parse_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise ProdigiClientError(
            f"Prodigi response was not valid JSON: {exc!r}",
            status_code=resp.status_code,
            body=_truncate(resp.text),
        ) from exc
    if not isinstance(data, dict):
        raise ProdigiClientError(
            "Prodigi response JSON was not an object",
            status_code=resp.status_code,
            body=_truncate(resp.text),
        )
    return data


def _extract_outcome(resp: httpx.Response) -> str | None:
    """Best-effort extraction of the ``outcome`` string from a response."""
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict):
        outcome = data.get("outcome")
        if isinstance(outcome, str):
            return outcome
    return None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header. Supports seconds-only form."""
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        # HTTP-date form (RFC 7231) is permitted but rare in practice; we
        # ignore it and fall back to exponential backoff.
        return None


def _truncate(s: str, limit: int = 4096) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [truncated {len(s) - limit} bytes]"


# ---------------------------------------------------------------------------
# Factory helpers — env-driven, lazy
# ---------------------------------------------------------------------------
_DEFAULT_SANDBOX_BASE = "https://api.sandbox.prodigi.com"
_DEFAULT_LIVE_BASE = "https://api.prodigi.com"


def get_sandbox_client() -> ProdigiClient:
    """Build a sandbox client from env (``PRODIGI_API_KEY_SANDBOX``).

    Raises ``RuntimeError`` if the env var is missing — this function is
    only called from code paths that genuinely need an API client.
    """
    api_key = os.environ.get("PRODIGI_API_KEY_SANDBOX")
    if not api_key:
        raise RuntimeError(
            "PRODIGI_API_KEY_SANDBOX is not set; cannot build sandbox client."
        )
    base = os.environ.get("PRODIGI_API_BASE_SANDBOX", _DEFAULT_SANDBOX_BASE)
    return ProdigiClient(api_key=api_key, base_url=base)


def get_live_client() -> ProdigiClient:
    """Build a live client from env (``PRODIGI_API_KEY_LIVE``)."""
    api_key = os.environ.get("PRODIGI_API_KEY_LIVE")
    if not api_key:
        raise RuntimeError(
            "PRODIGI_API_KEY_LIVE is not set; cannot build live client."
        )
    base = os.environ.get("PRODIGI_API_BASE_LIVE", _DEFAULT_LIVE_BASE)
    return ProdigiClient(api_key=api_key, base_url=base)


def get_default_client() -> ProdigiClient:
    """Return either the sandbox or live client based on ``PRODIGI_ENV``.

    Defaults to ``sandbox``. Set ``PRODIGI_ENV=live`` to switch to live.
    """
    env = os.environ.get("PRODIGI_ENV", "sandbox").strip().lower()
    if env == "live":
        return get_live_client()
    return get_sandbox_client()


__all__ = [
    "ProdigiClient",
    "ProdigiClientError",
    "get_default_client",
    "get_live_client",
    "get_sandbox_client",
]
