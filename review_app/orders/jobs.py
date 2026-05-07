"""Worker-side orchestration for converting an Order into a Prodigi order.

Triggered by an outbox row of kind ``prodigi.create_order`` (enqueued by the
v2 Stripe webhook handler in :mod:`review_app.checkout.webhook`). The outbox
drainer hands the payload (``{"order_id": "..."}``) to
:func:`create_prodigi_order_job`.

Job contract
------------
* Pure-function-shaped (RQ-callable, no closures).
* Idempotent — calling with the same ``order_id`` is safe. Idempotency is
  enforced two ways:
    1. Prodigi's own dedup via ``Idempotency-Key = f"wp-{order_id}"``.
    2. Our local check: if a ``ProdigiOrder`` row already exists with the
       same idempotency_key, we don't re-call Prodigi; we just confirm the
       order status and return.
* Pre-flight: walks each ``OrderItem``, looks up the tier-3 ``RenderOutputRow``,
  and refuses to call Prodigi if any item still lacks a tier-3 asset (raises
  :class:`RenderNotReadyError` so RQ retries the job).

Side effects
------------
* Updates ``orders.status = 'in_production'`` once Prodigi acknowledges.
* Inserts an outbox row of kind ``email.in_production``.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from review_app.orders.models import Order


_log = logging.getLogger(__name__)


# Default TTL for the signed Spaces URL we hand to Prodigi. 7 days fits
# Prodigi's 30-day asset retention with plenty of slack (per
# memory/project_prodigi_quirks.md).
_PRODIGI_ASSET_URL_TTL_SECONDS: int = 60 * 60 * 24 * 7


# ---------------------------------------------------------------------------
# Errors — surfaced to RQ so the job can be retried.
# ---------------------------------------------------------------------------
class OrderJobError(Exception):
    """Base class for create-prodigi-order job failures."""


class OrderNotFoundError(OrderJobError):
    """The order_id passed to the job doesn't exist."""


class RenderNotReadyError(OrderJobError):
    """At least one OrderItem doesn't yet have a tier-3 RenderOutputRow.

    Raised so RQ retries the job — the tier-3 render job is enqueued in
    parallel and may not have finished yet.
    """


class ProdigiCreateError(OrderJobError):
    """Prodigi returned a 4xx/5xx we couldn't recover from.

    Wraps ``ProdigiClientError`` so the outbox marks the row failed without
    coupling to the prodigi module's exception hierarchy.
    """


# ---------------------------------------------------------------------------
# Public entry — RQ-callable
# ---------------------------------------------------------------------------
def create_prodigi_order_job(order_id: str) -> dict[str, Any]:
    """Create the Prodigi order for one of our orders.

    See module docstring for the contract. Returns
    ``{"order_id": ..., "prodigi_order_id": ..., "outcome": ...}``.
    """
    # Late imports — keeps RQ's pickle-resolve fast and avoids dragging the
    # DB engine into the test process.
    from review_app.db import get_session

    with get_session() as session:
        result = _run(session, order_id)
    return result


def _run(session: Session, order_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from review_app.email.outbox import enqueue
    from review_app.orders.models import Order
    from review_app.prodigi.db_models import ProdigiOrder

    order = session.get(Order, _coerce_uuid(order_id))
    if order is None:
        raise OrderNotFoundError(f"Order {order_id!r} not found")

    idempotency_key = f"wp-{order.id}"

    # Local idempotency check.
    existing_stmt = select(ProdigiOrder).where(
        ProdigiOrder.idempotency_key == idempotency_key
    )
    existing = session.execute(existing_stmt).scalar_one_or_none()
    if existing is not None and existing.prodigi_order_id:
        _log.info(
            "create_prodigi_order_job: order %s already has Prodigi id %s",
            order.id, existing.prodigi_order_id,
        )
        return {
            "order_id": str(order.id),
            "prodigi_order_id": existing.prodigi_order_id,
            "outcome": "already_exists_local",
        }

    # Build the Prodigi request from order items + tier-3 assets.
    items_payload = _build_prodigi_items(session, order)

    address_payload = _build_prodigi_recipient(session, order)

    from review_app.prodigi import get_default_client
    from review_app.prodigi.client import ProdigiClientError
    from review_app.prodigi.models import OrderRequest

    request = OrderRequest.model_validate(
        {
            "shippingMethod": os.environ.get("PRODIGI_SHIPPING_METHOD", "Standard"),
            "merchantReference": str(order.id),
            "recipient": address_payload,
            "items": items_payload,
            "metadata": {
                "wildprint_order_id": str(order.id),
                "wildprint_customer_id": str(order.customer_id),
            },
        }
    )

    client = get_default_client()
    try:
        prodigi_order = client.create_order(request, idempotency_key=idempotency_key)
    except ProdigiClientError as exc:
        _log.error(
            "create_prodigi_order_job: Prodigi 4xx/5xx for order %s — %s",
            order.id, exc,
        )
        # Surface as a non-retryable job failure for 4xx; let RQ retry on 5xx.
        if exc.status_code is not None and 400 <= exc.status_code < 500:
            raise ProdigiCreateError(
                f"Prodigi rejected order {order.id}: HTTP {exc.status_code} {exc}"
            ) from exc
        raise  # propagate so RQ retries

    # Persist or update the prodigi_orders row. ``order_id`` is the FK
    # column added by migration 0011; the ORM model still uses the original
    # ``fishingposter_order_id`` field name (Phase 3a kept compat). We write
    # to fishingposter_order_id only — the migration's order_id stays NULL
    # for now and Phase 4 will reconcile.
    if existing is None:
        prow = ProdigiOrder(
            fishingposter_order_id=_coerce_uuid_for_dialect(session, order.id),
            prodigi_order_id=prodigi_order.id,
            idempotency_key=idempotency_key,
            status_stage=str(prodigi_order.status.stage) if prodigi_order.status else None,
            raw_snapshot=prodigi_order.model_dump(by_alias=True, exclude_none=True),
        )
        session.add(prow)
    else:
        existing.prodigi_order_id = prodigi_order.id
        existing.status_stage = (
            str(prodigi_order.status.stage) if prodigi_order.status else None
        )
        existing.raw_snapshot = prodigi_order.model_dump(by_alias=True, exclude_none=True)

    if order.status not in ("in_production", "shipped", "delivered"):
        order.status = "in_production"
        # Phase 5b — capture the moment we hand off to Prodigi for real
        # production. The operations analytics page (admin/analytics) reads
        # this column to compute AVG(in_production_at - paid_at).
        if getattr(order, "in_production_at", None) is None:
            from datetime import UTC as _UTC, datetime as _dt

            order.in_production_at = _dt.now(_UTC)

    # Fan out the next email step.
    customer_email = _customer_email(session, order)
    if customer_email:
        try:
            enqueue(
                session,
                kind="email.in_production",
                to=customer_email,
                payload={
                    "order_id": str(order.id),
                    "prodigi_order_id": prodigi_order.id,
                },
            )
        except Exception:
            _log.warning("could not enqueue email.in_production")

    return {
        "order_id": str(order.id),
        "prodigi_order_id": prodigi_order.id,
        "outcome": "created",
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _build_prodigi_items(session: Session, order: Order) -> list[dict[str, Any]]:
    """Build the Prodigi `items` array for an order.

    Each item gets a signed Spaces URL pointing at the tier-3 print asset.
    Raises :class:`RenderNotReadyError` if any item is missing tier-3.
    """
    from sqlalchemy import select

    from review_app.prodigi.db_models import ProdigiSku
    from review_app.render.db_models import RenderOutputRow
    from review_app.storage import get_signed_url

    if not order.items:
        raise OrderJobError(f"Order {order.id} has no items")

    sku_keys = {it.prodigi_sku_internal for it in order.items}
    sku_index: dict[str, ProdigiSku] = {
        s.internal_sku: s
        for s in session.execute(
            select(ProdigiSku).where(ProdigiSku.internal_sku.in_(sku_keys))
        ).scalars().all()
    }

    items_payload: list[dict[str, Any]] = []
    for oi in order.items:
        if oi.render_spec_id is None:
            raise OrderJobError(
                f"OrderItem {oi.id} has no render_spec_id — cannot fulfill"
            )
        out_stmt = (
            select(RenderOutputRow)
            .where(RenderOutputRow.render_spec_id == oi.render_spec_id)
            .where(RenderOutputRow.tier == 3)
            .order_by(RenderOutputRow.generated_at.desc())
            .limit(1)
        )
        out = session.execute(out_stmt).scalar_one_or_none()
        if out is None:
            raise RenderNotReadyError(
                f"OrderItem {oi.id}: tier-3 render not yet available "
                f"(render_spec_id={oi.render_spec_id})"
            )

        url = get_signed_url(
            bucket=out.storage_bucket,
            key=out.storage_key,
            expires_in=_PRODIGI_ASSET_URL_TTL_SECONDS,
        )

        sku = sku_index.get(oi.prodigi_sku_internal)
        if sku is None:
            raise OrderJobError(
                f"OrderItem {oi.id}: unknown prodigi_sku_internal {oi.prodigi_sku_internal!r}"
            )

        items_payload.append(
            {
                "merchantReference": str(oi.id),
                "sku": sku.prodigi_sku,
                "copies": oi.quantity,
                "sizing": "fillPrintArea",
                "assets": [
                    {
                        "printArea": "default",
                        "url": url,
                    }
                ],
                "attributes": {},
            }
        )
    return items_payload


def _build_prodigi_recipient(session: Session, order: Order) -> dict[str, Any]:
    """Build the Prodigi `recipient` payload from the order's shipping address."""
    if order.shipping_address_id is None:
        raise OrderJobError(f"Order {order.id} has no shipping_address_id")

    from review_app.addresses.models import Address

    addr = session.get(Address, order.shipping_address_id)
    if addr is None:
        raise OrderJobError(
            f"Order {order.id} references missing address {order.shipping_address_id}"
        )

    name = addr.name or _customer_name(session, order) or "Customer"

    return {
        "name": name,
        "address": {
            "line1": addr.line1,
            "line2": addr.line2,
            "townOrCity": addr.city,
            "stateOrCounty": addr.state,
            "postalOrZipCode": addr.zip,
            "countryCode": addr.country or "US",
        },
    }


def _customer_email(session: Session, order: Order) -> str | None:
    from review_app.customers.models import Customer

    customer = session.get(Customer, order.customer_id)
    return customer.email if customer else None


def _customer_name(session: Session, order: Order) -> str | None:
    from review_app.customers.models import Customer

    customer = session.get(Customer, order.customer_id)
    return customer.name if customer else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _coerce_uuid(value: str) -> Any:
    """Parse a UUID-shaped string. Lets the caller pass either str or UUID."""
    import uuid as _uuid

    if isinstance(value, _uuid.UUID):
        return value
    return _uuid.UUID(str(value))


def _coerce_uuid_for_dialect(session: Session, value: Any) -> Any:
    """Return ``value`` as a hex-string on SQLite (where prodigi tables use
    TEXT for UUID columns) and as a :class:`uuid.UUID` on Postgres.

    The Phase 1 ``prodigi_orders`` table uses
    ``UUID(as_uuid=True).with_variant(Text(), 'sqlite')`` which doesn't
    auto-stringify UUID instances under SQLite. Normalize at the boundary so
    the same code works against both backends.
    """
    import uuid as _uuid

    bind = session.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if isinstance(value, _uuid.UUID):
        return value.hex if is_sqlite else value
    return value


__all__ = [
    "OrderJobError",
    "OrderNotFoundError",
    "ProdigiCreateError",
    "RenderNotReadyError",
    "create_prodigi_order_job",
]
