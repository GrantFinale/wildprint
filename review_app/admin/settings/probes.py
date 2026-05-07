"""Real upstream service probes for the Settings/Integrations page.

Phase 5a replaces Phase 4a's "if env var is set, mark healthy" placeholder
with real outbound HTTP / SDK calls. Each probe is cached in-process for
5 minutes so the page doesn't hammer external APIs on every refresh.

Public surface:

* :class:`ProbeResult` — uniform result type.
* :func:`probe_prodigi`, :func:`probe_resend`, :func:`probe_smarty`,
  :func:`probe_spaces`, :func:`probe_stripe` — individual probes.
* :func:`probe_all` — runs every probe in parallel via ThreadPoolExecutor.
* :func:`reset_cache` — drops the in-memory cache (tests).

Each probe handles its own auth + the specific upstream contract; on
success it returns ``ProbeResult(ok=True, latency_ms=N, error=None)``,
on failure it captures the exception type+message in ``error`` and
returns ``ok=False``. Probes never raise; they always return a result.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type + cache
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single probe.

    Attributes
    ----------
    ok:
        True if the probe completed without errors.
    latency_ms:
        Round-trip time in milliseconds. ``None`` when the probe couldn't
        even reach the call site (missing env var, etc.).
    error:
        Stringified error message + type. ``None`` when ``ok`` is True.
    note:
        Optional free-form annotation (e.g. probe target SKU).
    """

    ok: bool
    latency_ms: float | None
    error: str | None
    note: str | None = None


_CACHE_TTL_SEC: Final[int] = 300

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, ProbeResult]] = {}


def reset_cache() -> None:
    """Drop every cached probe result. Called from tests + admin "refresh now"."""
    with _cache_lock:
        _cache.clear()


def _cached(name: str, callback: Any) -> ProbeResult:
    now = time.time()
    with _cache_lock:
        existing = _cache.get(name)
        if existing and (now - existing[0]) < _CACHE_TTL_SEC:
            return existing[1]
    # Run the probe outside the lock so concurrent probes don't serialize.
    result: ProbeResult = callback()
    with _cache_lock:
        _cache[name] = (time.time(), result)
    return result


def _ms(start: float) -> float:
    return round((time.time() - start) * 1000, 1)


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------
def probe_prodigi() -> ProbeResult:
    """GET ``/Products/{known_sku}`` against the Prodigi sandbox."""

    def _run() -> ProbeResult:
        api_key = os.environ.get("PRODIGI_SANDBOX_API_KEY", "").strip()
        if not api_key:
            return ProbeResult(
                ok=False,
                latency_ms=None,
                error="PRODIGI_SANDBOX_API_KEY not set",
            )

        sku = "GLOBAL-CFPM-16X20"
        url = f"https://api.sandbox.prodigi.com/v4.0/Products/{sku}"
        try:
            import httpx
        except ImportError:  # pragma: no cover - in requirements
            return ProbeResult(ok=False, latency_ms=None, error="httpx missing")

        start = time.time()
        try:
            resp = httpx.get(
                url,
                headers={"X-API-Key": api_key},
                timeout=8.0,
            )
            latency = _ms(start)
            if resp.status_code != 200:
                return ProbeResult(
                    ok=False,
                    latency_ms=latency,
                    error=f"HTTP {resp.status_code}",
                    note=f"sku={sku}",
                )
            return ProbeResult(ok=True, latency_ms=latency, error=None, note=f"sku={sku}")
        except Exception as exc:
            return ProbeResult(ok=False, latency_ms=_ms(start), error=_err(exc))

    return _cached("prodigi", _run)


def probe_resend() -> ProbeResult:
    """GET ``/domains`` against Resend's API."""

    def _run() -> ProbeResult:
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
        if not api_key:
            return ProbeResult(
                ok=False,
                latency_ms=None,
                error="RESEND_API_KEY not set",
            )

        try:
            import httpx
        except ImportError:  # pragma: no cover
            return ProbeResult(ok=False, latency_ms=None, error="httpx missing")

        start = time.time()
        try:
            resp = httpx.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=8.0,
            )
            latency = _ms(start)
            if resp.status_code not in (200, 401, 403):
                # 401/403 still proves we reached Resend.
                return ProbeResult(
                    ok=False,
                    latency_ms=latency,
                    error=f"HTTP {resp.status_code}",
                )
            ok = resp.status_code == 200
            error = None if ok else f"HTTP {resp.status_code}"
            return ProbeResult(ok=ok, latency_ms=latency, error=error)
        except Exception as exc:
            return ProbeResult(ok=False, latency_ms=_ms(start), error=_err(exc))

    return _cached("resend", _run)


def probe_smarty() -> ProbeResult:
    """Verify a known address against Smarty US Street API."""

    def _run() -> ProbeResult:
        auth_id = os.environ.get("SMARTY_AUTH_ID", "").strip()
        auth_token = os.environ.get("SMARTY_AUTH_TOKEN", "").strip()
        if not auth_id or not auth_token:
            return ProbeResult(
                ok=False,
                latency_ms=None,
                error="SMARTY_AUTH_ID + SMARTY_AUTH_TOKEN required",
            )

        try:
            import httpx
        except ImportError:  # pragma: no cover
            return ProbeResult(ok=False, latency_ms=None, error="httpx missing")

        start = time.time()
        try:
            resp = httpx.get(
                "https://us-street.api.smarty.com/street-address",
                params={
                    "auth-id": auth_id,
                    "auth-token": auth_token,
                    "street": "1 Apple Park Way",
                    "city": "Cupertino",
                    "state": "CA",
                    "candidates": "1",
                },
                timeout=8.0,
            )
            latency = _ms(start)
            if resp.status_code != 200:
                return ProbeResult(
                    ok=False,
                    latency_ms=latency,
                    error=f"HTTP {resp.status_code}",
                )
            payload = resp.json()
            note = (
                "1 candidate"
                if isinstance(payload, list) and len(payload) >= 1
                else "0 candidates"
            )
            return ProbeResult(ok=True, latency_ms=latency, error=None, note=note)
        except Exception as exc:
            return ProbeResult(ok=False, latency_ms=_ms(start), error=_err(exc))

    return _cached("smarty", _run)


def probe_spaces() -> ProbeResult:
    """``list_objects_v2`` against the Spaces thumbs bucket (limit=1)."""

    def _run() -> ProbeResult:
        bucket = os.environ.get("SPACES_THUMBS_BUCKET", "").strip()
        if not bucket:
            return ProbeResult(
                ok=False,
                latency_ms=None,
                error="SPACES_THUMBS_BUCKET not set",
            )

        try:
            from review_app.storage import spaces
        except ImportError:  # pragma: no cover
            return ProbeResult(ok=False, latency_ms=None, error="spaces module missing")

        start = time.time()
        try:
            # Module-private accessor; tested by patching this attribute.
            client = spaces._client()
            resp = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
            latency = _ms(start)
            return ProbeResult(
                ok=True,
                latency_ms=latency,
                error=None,
                note=f"{resp.get('KeyCount', 0)} key(s) seen",
            )
        except Exception as exc:
            return ProbeResult(ok=False, latency_ms=_ms(start), error=_err(exc))

    return _cached("spaces", _run)


def probe_stripe() -> ProbeResult:
    """Stripe ``Account.retrieve()`` — proves the secret key is valid."""

    def _run() -> ProbeResult:
        api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not api_key:
            return ProbeResult(
                ok=False,
                latency_ms=None,
                error="STRIPE_SECRET_KEY not set",
            )

        try:
            import stripe
        except ImportError:  # pragma: no cover
            return ProbeResult(ok=False, latency_ms=None, error="stripe SDK missing")

        stripe.api_key = api_key
        start = time.time()
        try:
            account = stripe.Account.retrieve()
            latency = _ms(start)
            account_id = getattr(account, "id", None) or "unknown"
            return ProbeResult(
                ok=True,
                latency_ms=latency,
                error=None,
                note=f"account={account_id}",
            )
        except Exception as exc:
            return ProbeResult(ok=False, latency_ms=_ms(start), error=_err(exc))

    return _cached("stripe", _run)


# ---------------------------------------------------------------------------
# Aggregator — runs probes in parallel
# ---------------------------------------------------------------------------
PROBES: Final[dict[str, Any]] = {
    "Stripe": probe_stripe,
    "Resend": probe_resend,
    "Prodigi": probe_prodigi,
    "Smarty": probe_smarty,
    "DO Spaces": probe_spaces,
}


def probe_all(*, max_workers: int = 5) -> dict[str, ProbeResult]:
    """Run every probe in parallel; return ``{service: ProbeResult}``."""
    out: dict[str, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn): name for name, fn in PROBES.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception as exc:  # pragma: no cover - probe never raises
                out[name] = ProbeResult(
                    ok=False, latency_ms=None, error=_err(exc)
                )
    return out


__all__ = [
    "PROBES",
    "ProbeResult",
    "probe_all",
    "probe_prodigi",
    "probe_resend",
    "probe_smarty",
    "probe_spaces",
    "probe_stripe",
    "reset_cache",
]
