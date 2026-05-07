"""Smarty US Street Address validation client.

Hits ``https://us-street.api.smarty.com/street-address`` with ``match=invalid``
so even un-deliverable addresses come back with their analysis — we use the
DPV (Delivery Point Validation) match code to decide whether to ship.

Authentication is via ``SMARTY_AUTH_ID`` + ``SMARTY_AUTH_TOKEN`` env vars;
both are read lazily inside :func:`verify_address` so importing this module
never fails (and tests can monkeypatch the env without re-importing).

Reference: https://www.smarty.com/docs/cloud/us-street-api
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from review_app.observability import get_logger

log = get_logger(__name__)

SMARTY_BASE_URL: str = "https://us-street.api.smarty.com/street-address"

# DPV codes that indicate the address is good to ship to. Mirror the constant
# in ``review_app.addresses.models`` to avoid a circular import.
DELIVERABLE_DPV_CODES: frozenset[str] = frozenset({"Y", "S", "D"})

# Conservative HTTP timeouts. Smarty is fast (<200ms p95) so a generous
# budget here just exists to absorb network blips.
DEFAULT_CONNECT_TIMEOUT_S: float = 5.0
DEFAULT_READ_TIMEOUT_S: float = 10.0


class SmartyError(RuntimeError):
    """Wraps any failure talking to the Smarty US Street API.

    The original underlying exception is preserved on ``__cause__`` (raise
    via ``raise SmartyError(...) from exc``). HTTP failures expose the
    response status on the ``status_code`` attribute.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SmartyResult(BaseModel):
    """Typed parsed response for one address verification call."""

    is_deliverable: bool = Field(
        ..., description="True if dpv_match_code in {'Y','S','D'}."
    )
    dpv_match_code: str = Field(
        default="",
        description="USPS DPV code: 'Y'=valid, 'S'=secondary missing, "
        "'D'=secondary missing-not-required, 'N'=invalid, ''=not returned.",
    )
    standardized_line1: str = ""
    standardized_city: str = ""
    standardized_state: str = ""
    standardized_zip: str = ""
    standardized_zip_plus4: str = ""
    raw_response: dict[str, Any] = Field(default_factory=dict)


def _build_url(
    *,
    line1: str,
    line2: str | None,
    city: str,
    state: str,
    zip_code: str,
    country: str,
    auth_id: str,
    auth_token: str,
) -> str:
    """Compose the full GET URL with auth + payload params.

    Smarty's US Street API takes either GET (single lookup) or POST (batch).
    Single lookups via GET keep this client dependency-free (stdlib urllib).
    """
    params: dict[str, str] = {
        "auth-id": auth_id,
        "auth-token": auth_token,
        # ``match=invalid`` returns analysis even for un-deliverable addresses.
        "match": "invalid",
        "candidates": "1",
        "street": line1,
        "city": city,
        "state": state,
        "zipcode": zip_code,
    }
    if line2:
        params["secondary"] = line2
    # Smarty's US Street API is US-only; ``country`` is informational here
    # but we forward it so non-US calls can be filtered upstream.
    if country and country.upper() != "US":
        # Caller error — this client is US-only. Surface fast.
        raise SmartyError(
            f"smarty.us_street is US-only; got country={country!r}"
        )

    encoded = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{SMARTY_BASE_URL}?{encoded}"


def _read_credentials() -> tuple[str, str]:
    """Lazily resolve Smarty credentials from env. Raise SmartyError if unset."""
    auth_id = os.environ.get("SMARTY_AUTH_ID", "").strip()
    auth_token = os.environ.get("SMARTY_AUTH_TOKEN", "").strip()
    if not auth_id or not auth_token:
        raise SmartyError(
            "SMARTY_AUTH_ID / SMARTY_AUTH_TOKEN env vars are not set."
        )
    return auth_id, auth_token


def _parse_response(raw: list[dict[str, Any]] | dict[str, Any]) -> SmartyResult:
    """Convert Smarty's JSON response into a SmartyResult.

    Smarty returns a JSON array (possibly empty). The first element is the
    best candidate; we ignore the rest because we always pass ``candidates=1``.
    """
    # Defensive — older API versions occasionally wrap the array in an object.
    candidates = (raw.get("candidates") or []) if isinstance(raw, dict) else raw

    if not candidates:
        return SmartyResult(is_deliverable=False, raw_response={"candidates": []})

    first = candidates[0]
    components: dict[str, Any] = first.get("components", {}) or {}
    metadata: dict[str, Any] = first.get("metadata", {}) or {}
    analysis: dict[str, Any] = first.get("analysis", {}) or {}

    dpv_code: str = (analysis.get("dpv_match_code") or "").strip()

    standardized_line1 = (first.get("delivery_line_1") or "").strip()
    standardized_city = (components.get("city_name") or "").strip()
    standardized_state = (components.get("state_abbreviation") or "").strip()
    standardized_zip = (components.get("zipcode") or "").strip()
    standardized_zip_plus4 = (components.get("plus4_code") or "").strip()
    if not standardized_zip and metadata.get("zip_type"):
        # Some payloads put zip in metadata.
        standardized_zip = (metadata.get("zipcode") or "").strip()

    return SmartyResult(
        is_deliverable=dpv_code in DELIVERABLE_DPV_CODES,
        dpv_match_code=dpv_code,
        standardized_line1=standardized_line1,
        standardized_city=standardized_city,
        standardized_state=standardized_state,
        standardized_zip=standardized_zip,
        standardized_zip_plus4=standardized_zip_plus4,
        raw_response={"candidates": candidates},
    )


def verify_address(
    line1: str,
    line2: str | None,
    city: str,
    state: str,
    zip_code: str,
    country: str = "US",
    *,
    timeout_s: float = DEFAULT_READ_TIMEOUT_S,
) -> SmartyResult:
    """Look up one US address against the Smarty US Street API.

    Returns a populated :class:`SmartyResult`. Raises :class:`SmartyError`
    on transport/HTTP failures. Even un-deliverable addresses return a
    SmartyResult (with ``is_deliverable=False`` and the DPV code) — only
    transport failures raise.

    Logs every call via structlog: event ``address_verify_call`` with
    input fields, ``dpv_code``, and ``latency_ms``.
    """
    auth_id, auth_token = _read_credentials()
    url = _build_url(
        line1=line1,
        line2=line2,
        city=city,
        state=state,
        zip_code=zip_code,
        country=country,
        auth_id=auth_id,
        auth_token=auth_token,
    )

    start_t = time.perf_counter()
    body: bytes | None = None
    status_code: int | None = None
    try:
        # urllib has only one combined timeout; we use the read timeout.
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            status_code = int(resp.status)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.perf_counter() - start_t) * 1000)
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        log.warning(
            "address_verify_call",
            line1=line1,
            city=city,
            state=state,
            zip=zip_code,
            dpv_code=None,
            status=exc.code,
            latency_ms=latency_ms,
            error=str(exc),
        )
        raise SmartyError(
            f"Smarty HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
            body=err_body,
        ) from exc
    except urllib.error.URLError as exc:
        latency_ms = int((time.perf_counter() - start_t) * 1000)
        log.warning(
            "address_verify_call",
            line1=line1,
            city=city,
            state=state,
            zip=zip_code,
            dpv_code=None,
            latency_ms=latency_ms,
            error=str(exc),
        )
        raise SmartyError(f"Smarty transport error: {exc.reason}") from exc

    latency_ms = int((time.perf_counter() - start_t) * 1000)

    try:
        decoded: Any = json.loads((body or b"").decode("utf-8") or "[]")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning(
            "address_verify_call",
            line1=line1,
            city=city,
            state=state,
            zip=zip_code,
            dpv_code=None,
            status=status_code,
            latency_ms=latency_ms,
            error=f"json_decode: {exc}",
        )
        raise SmartyError("Smarty returned non-JSON body") from exc

    result = _parse_response(decoded)
    log.info(
        "address_verify_call",
        line1=line1,
        city=city,
        state=state,
        zip=zip_code,
        dpv_code=result.dpv_match_code or None,
        deliverable=result.is_deliverable,
        status=status_code,
        latency_ms=latency_ms,
    )
    return result


__all__ = [
    "DELIVERABLE_DPV_CODES",
    "SMARTY_BASE_URL",
    "SmartyError",
    "SmartyResult",
    "verify_address",
]
