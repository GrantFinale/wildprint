"""Stripe Tax integration for cart-driven checkout.

Phase 5a opt-in tax computation. Wraps ``stripe.tax.Calculation.create`` so
the checkout flow can quote tax for a US shipping address before creating
the actual Checkout Session.

Design:

* OPT-IN via env flag ``STRIPE_TAX_ENABLED`` (default off). When disabled
  every public function returns a zeroed result and the caller proceeds as
  before — Phase 3b checkout behavior is preserved bit-for-bit.
* Stripe Tax is billed per call. We dedupe identical (line items + address)
  pairs in an in-memory cache for 5 minutes, which absorbs the typical
  "user toggles a quantity in the cart UI" loop without re-quoting.
* All errors funnel through :class:`TaxComputationError` so the route layer
  has a single exception type to translate to HTTP.

Public surface
--------------
* :func:`compute_tax_for_session` — main entry point.
* :func:`is_enabled` — read the env flag (also useful in admin views).
* :func:`reset_cache` — drop the in-memory cache (used by tests).
* :func:`recent_errors` — last 10 errors for the admin dashboard.
* :class:`TaxComputationError` — wraps every failure mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env flag
# ---------------------------------------------------------------------------
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def is_enabled() -> bool:
    """Return True when ``STRIPE_TAX_ENABLED`` is set to a truthy value."""
    return os.environ.get("STRIPE_TAX_ENABLED", "").strip().lower() in _TRUE_VALUES


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TaxComputationError(RuntimeError):
    """Raised for any failure inside :func:`compute_tax_for_session`.

    Wraps the underlying Stripe SDK exception (or other RuntimeError) so
    callers don't need to import ``stripe`` to catch it.
    """


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaxResult:
    """Outcome of a tax computation call.

    Attributes
    ----------
    enabled:
        Whether Stripe Tax is enabled (i.e. ``STRIPE_TAX_ENABLED=true``).
        When False, ``tax_amount_cents`` is always 0 and ``breakdown`` is
        an empty list.
    tax_amount_cents:
        Total tax owed in the smallest currency unit (cents for USD).
    breakdown:
        Per-jurisdiction tax breakdown as returned by Stripe. Empty when
        ``enabled`` is False.
    calculation_id:
        Stripe's calculation identifier. ``None`` when disabled or for
        cached responses where we returned the original id.
    """

    enabled: bool
    tax_amount_cents: int
    breakdown: list[dict[str, Any]]
    calculation_id: str | None


def _disabled_result() -> TaxResult:
    return TaxResult(
        enabled=False,
        tax_amount_cents=0,
        breakdown=[],
        calculation_id=None,
    )


# Backward-compatible alias for older callers that expect a plain dict.
def result_as_dict(result: TaxResult) -> dict[str, Any]:
    """Coerce a :class:`TaxResult` to the legacy ``{'tax_amount_cents': N, 'enabled': bool, ...}`` dict."""
    return {
        "enabled": result.enabled,
        "tax_amount_cents": result.tax_amount_cents,
        "breakdown": result.breakdown,
        "calculation_id": result.calculation_id,
    }


# ---------------------------------------------------------------------------
# Cache (in-memory, 5-min TTL)
# ---------------------------------------------------------------------------
_CACHE_TTL_SEC: Final[int] = 300

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, TaxResult]] = {}

# Last-N errors for the admin dashboard (oldest first).
_errors_lock = threading.Lock()
_recent_errors: deque[dict[str, Any]] = deque(maxlen=10)


def reset_cache() -> None:
    """Drop the in-memory cache. Tests call this between runs."""
    with _cache_lock:
        _cache.clear()
    with _errors_lock:
        _recent_errors.clear()


def recent_errors() -> list[dict[str, Any]]:
    """Snapshot of recent tax-computation errors (newest first)."""
    with _errors_lock:
        # newest first
        return list(reversed(_recent_errors))


def _record_error(error_msg: str) -> None:
    with _errors_lock:
        _recent_errors.append({"ts": time.time(), "error": error_msg})


# ---------------------------------------------------------------------------
# Hashing — cache key
# ---------------------------------------------------------------------------
def _normalize_line_items(line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort + project line items down to the fields that affect tax."""
    out: list[dict[str, Any]] = []
    for li in line_items:
        out.append(
            {
                "amount": int(li.get("amount", 0)),
                "quantity": int(li.get("quantity", 1)),
                "reference": str(li.get("reference", "")),
                "tax_code": str(li.get("tax_code", "")),
            }
        )
    out.sort(key=lambda d: (d["reference"], d["amount"], d["quantity"]))
    return out


def _normalize_address(addr: dict[str, Any]) -> dict[str, str]:
    """Project an address to canonical form for hashing."""
    return {
        "line1": str(addr.get("line1", "") or addr.get("address", "")).strip().lower(),
        "line2": str(addr.get("line2", "")).strip().lower(),
        "city": str(addr.get("city", "")).strip().lower(),
        "state": str(addr.get("state", "")).strip().upper(),
        "postal_code": str(
            addr.get("postal_code", "") or addr.get("zip_code", "") or addr.get("zip", "")
        ).strip(),
        "country": str(addr.get("country", "US")).strip().upper(),
    }


def _cache_key(line_items: list[dict[str, Any]], address: dict[str, Any]) -> str:
    payload = {
        "items": _normalize_line_items(line_items),
        "addr": _normalize_address(address),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_tax_for_session(
    line_items: list[dict[str, Any]],
    shipping_address: dict[str, Any],
    *,
    currency: str = "usd",
    customer_id: str | None = None,
) -> TaxResult:
    """Quote tax for a cart + US shipping address using Stripe Tax.

    Parameters
    ----------
    line_items:
        Each item must contain at minimum ``amount`` (int cents) and
        ``quantity`` (int). Optional ``reference`` (str) and ``tax_code``
        (Stripe product tax code, e.g. ``txcd_99999999``) are passed
        through. ``amount`` is the line total in cents, NOT the unit price.
    shipping_address:
        Dict with keys ``line1`` / ``line2`` / ``city`` / ``state`` /
        ``postal_code`` / ``country``. We accept ``zip_code`` /
        ``zip`` / ``address`` as legacy aliases.
    currency:
        ISO-4217 lowercased — ``usd`` is the only one we ship in Phase 5a.
    customer_id:
        Optional Stripe customer id for tax-exempt lookups.

    Returns
    -------
    A :class:`TaxResult`. When ``STRIPE_TAX_ENABLED`` is false this returns
    a zero result without touching Stripe.

    Raises
    ------
    TaxComputationError:
        If Stripe Tax returns an error or the SDK is missing.
    """
    if not is_enabled():
        return _disabled_result()

    if not line_items:
        # Stripe rejects empty carts; return zero rather than 4xx so the
        # caller can decide how to handle empty carts.
        return TaxResult(
            enabled=True,
            tax_amount_cents=0,
            breakdown=[],
            calculation_id=None,
        )

    cache_key = _cache_key(line_items, shipping_address)
    now = time.time()

    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]

    addr = _normalize_address(shipping_address)
    if addr["country"] != "US":
        # Phase 5a US-only.
        raise TaxComputationError(
            f"Stripe Tax integration ships US-only in Phase 5a (got country={addr['country']!r})"
        )

    try:
        import stripe
    except ImportError as exc:  # pragma: no cover - stripe is in requirements.txt
        raise TaxComputationError(
            "stripe SDK is not installed; cannot call Stripe Tax."
        ) from exc

    api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not api_key:
        raise TaxComputationError(
            "STRIPE_SECRET_KEY env var is required when STRIPE_TAX_ENABLED=true."
        )
    stripe.api_key = api_key

    # Stripe Tax expects ``customer_details.address`` with country/region/postal.
    customer_details: dict[str, Any] = {
        "address": {
            "line1": addr["line1"] or None,
            "line2": addr["line2"] or None,
            "city": addr["city"] or None,
            "state": addr["state"] or None,
            "postal_code": addr["postal_code"] or None,
            "country": addr["country"],
        },
        "address_source": "shipping",
    }

    api_line_items: list[dict[str, Any]] = []
    for li in line_items:
        api_li: dict[str, Any] = {
            "amount": int(li.get("amount", 0)),
            "quantity": int(li.get("quantity", 1)),
        }
        ref = li.get("reference")
        if ref:
            api_li["reference"] = str(ref)
        tax_code = li.get("tax_code")
        if tax_code:
            api_li["tax_code"] = str(tax_code)
        api_line_items.append(api_li)

    try:
        # The SDK type stubs for ``stripe.tax.Calculation.create`` are
        # strict; we want to pass plain dicts. Cast through Any to keep
        # the call site readable.
        kwargs: dict[str, Any] = {
            "currency": currency,
            "line_items": api_line_items,
            "customer_details": customer_details,
        }
        if customer_id:
            kwargs["customer"] = customer_id
        _create = cast(Any, stripe.tax.Calculation).create
        calc = _create(**kwargs)
    except Exception as exc:
        msg = f"Stripe Tax calculation failed: {type(exc).__name__}: {exc}"
        logger.warning(msg)
        _record_error(msg)
        raise TaxComputationError(msg) from exc

    calc_dict = _to_dict(calc)
    tax_amount = int(calc_dict.get("tax_amount_exclusive", 0) or 0)
    breakdown_raw = calc_dict.get("tax_breakdown") or []
    breakdown: list[dict[str, Any]] = [
        _to_dict(item) for item in breakdown_raw
    ]
    calculation_id_raw = calc_dict.get("id")
    calculation_id: str | None = (
        str(calculation_id_raw) if calculation_id_raw is not None else None
    )

    result = TaxResult(
        enabled=True,
        tax_amount_cents=tax_amount,
        breakdown=breakdown,
        calculation_id=calculation_id,
    )

    with _cache_lock:
        _cache[cache_key] = (now, result)

    return result


def _to_dict(obj: Any) -> dict[str, Any]:
    """Coerce a Stripe SDK object to a plain dict.

    Mirrors the helper in ``stripe_client.py`` — duplicated here to avoid a
    circular import (``stripe_client`` imports nothing from this module but
    we want to remain importable in isolation).
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            return cast(dict[str, Any], obj.to_dict())
        except Exception:
            pass
    out: dict[str, Any] = {}
    for k in dir(obj):
        if k.startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:
            continue
        if callable(v):
            continue
        out[k] = v
    return out


def line_items_from_cart(cart: Any) -> list[dict[str, Any]]:
    """Build Stripe-Tax line items from a :class:`CartDTO`.

    Helper kept here so ``checkout.routes`` doesn't have to know the line
    item schema. Each cart item becomes one tax line; ``amount`` is the
    line total (unit_price * quantity).
    """
    items: list[dict[str, Any]] = []
    for ci in cart.items:
        items.append(
            {
                "amount": int(ci.unit_price_cents) * int(ci.quantity),
                "quantity": int(ci.quantity),
                "reference": str(ci.id),
                # Default tax code: General — Tangible Goods.
                "tax_code": "txcd_99999999",
            }
        )
    return items


def address_from_db(address: Any) -> dict[str, Any]:
    """Project an :class:`Address` SQLA model to the Stripe Tax address shape."""
    return {
        "line1": getattr(address, "line1", "") or "",
        "line2": getattr(address, "line2", "") or "",
        "city": getattr(address, "city", "") or "",
        "state": getattr(address, "state", "") or "",
        "postal_code": (
            getattr(address, "postal_code", "")
            or getattr(address, "zip_code", "")
            or ""
        ),
        "country": getattr(address, "country", "US") or "US",
    }


__all__ = [
    "TaxComputationError",
    "TaxResult",
    "address_from_db",
    "compute_tax_for_session",
    "is_enabled",
    "line_items_from_cart",
    "recent_errors",
    "reset_cache",
    "result_as_dict",
]
