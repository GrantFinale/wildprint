"""Nightly quote refresh — keeps ``prodigi_skus`` wholesale prices fresh.

For every active row in ``prodigi_skus`` we POST a single-item /Quotes
request against the Prodigi API and write back ``last_quoted_wholesale_cents``
+ ``last_refreshed_at``. Failures are logged per-SKU but never abort the
batch — a single bad row shouldn't poison nightly pricing for the rest.

Designed to be enqueued nightly. Cron scheduling is Phase 5; for now any
scheduler (RQ-Scheduler, system cron, manual run) can call
:func:`refresh_all_skus_job` directly.

Usage::

    from review_app.prodigi.quote_refresh import refresh_all_skus_job
    summary = refresh_all_skus_job()
    # {"checked": 32, "succeeded": 28, "failed": 4}
"""
from __future__ import annotations

from datetime import UTC, datetime

from review_app.observability import get_logger
from review_app.prodigi.client import ProdigiClient, ProdigiClientError, get_default_client
from review_app.prodigi.db_models import ProdigiSku
from review_app.prodigi.models import (
    Asset,
    Item,
    QuoteRequest,
)

_log = get_logger("prodigi.quote_refresh")


# Quotes are scoped to a destination country — for Phase 1 we ship to US only.
_DEFAULT_QUOTE_DESTINATION_COUNTRY = "US"


def refresh_all_skus_job(
    *,
    client: ProdigiClient | None = None,
) -> dict[str, int]:
    """Refresh wholesale pricing for every active row in ``prodigi_skus``.

    Returns a summary dict with keys ``checked``, ``succeeded``, ``failed``.
    """
    from review_app.db import get_session_factory

    session_factory = get_session_factory()
    session = session_factory()
    api = client or get_default_client()
    summary = {"checked": 0, "succeeded": 0, "failed": 0}
    try:
        skus = (
            session.query(ProdigiSku)
            .filter(ProdigiSku.active.is_(True))
            .all()
        )
        for sku in skus:
            summary["checked"] += 1
            try:
                cents = _quote_one(api, sku)
            except ProdigiClientError as exc:
                summary["failed"] += 1
                _log.warning(
                    "prodigi_quote_refresh_failed",
                    internal_sku=sku.internal_sku,
                    prodigi_sku=sku.prodigi_sku,
                    finish=sku.finish,
                    status_code=exc.status_code,
                    error=str(exc)[:200],
                )
                continue
            except Exception as exc:
                summary["failed"] += 1
                _log.warning(
                    "prodigi_quote_refresh_unexpected_error",
                    internal_sku=sku.internal_sku,
                    error=type(exc).__name__,
                )
                continue

            sku.last_quoted_wholesale_cents = cents
            sku.last_refreshed_at = datetime.now(UTC)
            if sku.retail_price_cents is not None and cents is not None:
                sku.margin_cents = max(sku.retail_price_cents - cents, 0)
            summary["succeeded"] += 1
            _log.info(
                "prodigi_quote_refresh_ok",
                internal_sku=sku.internal_sku,
                prodigi_sku=sku.prodigi_sku,
                wholesale_cents=cents,
            )
        session.commit()
    finally:
        session.close()

    _log.info("prodigi_quote_refresh_done", **summary)
    return summary


def _quote_one(api: ProdigiClient, sku: ProdigiSku) -> int | None:
    """Issue one /Quotes request for the SKU and return wholesale cents.

    Picks the cheapest quote across all returned shipping methods. Returns
    None when no quotes were returned (e.g. SKU doesn't ship to US).
    """
    item = Item(
        sku=sku.prodigi_sku,
        copies=1,
        attributes={"color": _finish_to_color_attr(sku.finish)},
        # Asset URL is not required for quotes per Prodigi docs; passing only
        # the printArea avoids HEAD-resolving placeholder URLs that the
        # sandbox occasionally rejects.
        assets=[Asset(printArea="default")],
    )
    request = QuoteRequest(
        shippingMethod="Budget",
        destinationCountryCode=_DEFAULT_QUOTE_DESTINATION_COUNTRY,
        currencyCode="USD",
        items=[item],
    )
    response = api.quote(request)
    if not response.quotes:
        return None
    cents_options: list[int] = []
    for q in response.quotes:
        cs = q.cost_summary
        if cs is None:
            continue
        # Prefer totalCost when present, else sum items + shipping.
        if cs.total_cost is not None:
            cents_options.append(cs.total_cost.amount_cents())
            continue
        items_cents = cs.items.amount_cents() if cs.items else 0
        shipping_cents = cs.shipping.amount_cents() if cs.shipping else 0
        cents_options.append(items_cents + shipping_cents)
    if not cents_options:
        return None
    return min(cents_options)


def _finish_to_color_attr(finish: str) -> str:
    """Map our display finish name to Prodigi's ``color`` attribute value.

    Prodigi's ``color`` attribute uses lowercase, space-separated strings
    like ``"dark grey"`` and ``"light grey"``. The ``Antique Silver`` and
    ``Antique Gold`` marketing names correspond to plain ``silver`` and
    ``gold`` in Prodigi's catalog. Verified via GET /v4.0/products/
    GLOBAL-CFPM-16X20 in sandbox.
    """
    lookup = {
        "antique silver": "silver",
        "antique gold": "gold",
    }
    key = finish.lower().strip()
    return lookup.get(key, key)


__all__ = ["refresh_all_skus_job"]
