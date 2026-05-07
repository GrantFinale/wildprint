"""Unit tests for :mod:`review_app.orders.jobs`.

External services mocked at the module boundary:
  - ``review_app.prodigi.get_default_client`` — fake client returning a
    typed Prodigi ``Order``.
  - ``review_app.storage.get_signed_url`` — returns a deterministic URL.
  - ``review_app.db.get_session`` — context manager yielding our test session.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from review_app.orders import jobs as orders_jobs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def order_setup(db_session: "Session") -> dict[str, Any]:
    """Build a minimal Order with one OrderItem + tier-3 RenderOutputRow."""
    from review_app.addresses.models import Address
    from review_app.customers.models import Customer
    from review_app.orders.models import Order, OrderItem
    from review_app.prodigi.db_models import ProdigiSku
    from review_app.render.db_models import RenderOutputRow, RenderSpecRow

    sku = ProdigiSku(
        internal_sku="FP-CLA-16X20-NAT",
        prodigi_sku="GLOBAL-CFPM-16X20",
        finish="Natural",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=12900,
        in_stock=True,
    )
    spec = RenderSpecRow(
        spec_hash="c" * 64,
        canonical_inputs={
            "lake": "Test",
            "species": [],
            "art_style": "x",
            "layout_config": {},
            "renderer_version": "v1",
        },
        renderer_version="v1",
    )
    cust = Customer.create(email="job@example.com", name="Job Test")
    db_session.add_all([sku, spec, cust])
    db_session.flush()

    addr = Address(
        customer_id=cust.id,
        line1="1 Main",
        city="Portland",
        state="OR",
        zip="97201",
        country="US",
        validated_at=datetime.now(UTC),
        validation_provider="smarty",
        dpv_match_code="Y",
    )
    db_session.add(addr)
    db_session.flush()

    order = Order(
        customer_id=cust.id,
        shipping_address_id=addr.id,
        stripe_payment_intent_id="pi_jobtest_001",
        status="paid",
        subtotal_cents=12900,
        total_cents=12900,
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    db_session.add(order)
    db_session.flush()

    oi = OrderItem(
        order_id=order.id,
        render_spec_id=spec.id,
        prodigi_sku_internal=sku.internal_sku,
        quantity=2,
        unit_price_cents=12900,
        line_total_cents=25800,
        finish_display="Natural",
        size_inches="16x20",
    )
    db_session.add(oi)
    db_session.flush()

    out = RenderOutputRow(
        render_spec_id=spec.id,
        tier=3,
        storage_bucket="wildprint-tier3",
        storage_key=f"prints/{spec.spec_hash}.png",
        file_size_bytes=1024,
    )
    db_session.add(out)
    db_session.flush()

    return {
        "order": order,
        "order_item": oi,
        "sku": sku,
        "spec": spec,
        "render_output": out,
        "customer": cust,
        "address": addr,
    }


@pytest.fixture()
def patched_externals(
    db_session: "Session", monkeypatch: pytest.MonkeyPatch
) -> MagicMock:
    """Patch get_session, get_signed_url, and get_default_client.

    Returns the MagicMock for the prodigi client so individual tests can
    customize ``client.create_order.return_value``.
    """
    @contextmanager
    def _fake_get_session() -> Any:
        yield db_session

    monkeypatch.setattr("review_app.db.get_session", _fake_get_session)
    # The job imports lazily — also patch where it's bound at call time.
    monkeypatch.setattr(
        "review_app.storage.get_signed_url",
        lambda **kw: f"https://signed.example/{kw['key']}",
    )

    # Build a fake Prodigi Order to return from create_order.
    from review_app.prodigi.models import (
        Address as PAddress,
        Order as POrder,
        Recipient,
        Status,
    )

    fake_order = POrder.model_validate(
        {
            "id": "ord_fake_123",
            "shippingMethod": "Standard",
            "status": {"stage": "InProgress", "issues": []},
            "recipient": {
                "name": "Job Test",
                "address": {
                    "line1": "1 Main",
                    "townOrCity": "Portland",
                    "stateOrCounty": "OR",
                    "postalOrZipCode": "97201",
                    "countryCode": "US",
                },
            },
            "items": [],
        }
    )

    fake_client = MagicMock()
    fake_client.create_order.return_value = fake_order
    monkeypatch.setattr(
        "review_app.prodigi.get_default_client", lambda: fake_client
    )
    return fake_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_create_prodigi_order_job_idempotent_under_retry(
    order_setup: dict[str, Any],
    patched_externals: MagicMock,
    db_session: "Session",
) -> None:
    """Running the job twice returns the same Prodigi order id and only
    calls Prodigi once on the second attempt (local idempotency)."""
    order = order_setup["order"]

    r1 = orders_jobs.create_prodigi_order_job(str(order.id))
    assert r1["prodigi_order_id"] == "ord_fake_123"
    assert r1["outcome"] == "created"
    assert patched_externals.create_order.call_count == 1

    r2 = orders_jobs.create_prodigi_order_job(str(order.id))
    assert r2["prodigi_order_id"] == "ord_fake_123"
    assert r2["outcome"] == "already_exists_local"
    # Local dedup avoided the second Prodigi call.
    assert patched_externals.create_order.call_count == 1


def test_create_prodigi_order_job_waits_for_render_tier_3(
    order_setup: dict[str, Any],
    patched_externals: MagicMock,
    db_session: "Session",
) -> None:
    """If the tier-3 render isn't ready, the job raises so RQ retries."""
    from sqlalchemy import delete

    from review_app.render.db_models import RenderOutputRow

    db_session.execute(delete(RenderOutputRow))
    db_session.flush()

    order = order_setup["order"]
    with pytest.raises(orders_jobs.RenderNotReadyError):
        orders_jobs.create_prodigi_order_job(str(order.id))
    patched_externals.create_order.assert_not_called()


def test_create_prodigi_order_job_handles_prodigi_4xx_gracefully(
    order_setup: dict[str, Any],
    patched_externals: MagicMock,
    db_session: "Session",
) -> None:
    """A 4xx from Prodigi raises ProdigiCreateError (non-retryable)."""
    from review_app.prodigi.client import ProdigiClientError

    patched_externals.create_order.side_effect = ProdigiClientError(
        "bad request", status_code=400, body="invalid sku"
    )
    order = order_setup["order"]
    with pytest.raises(orders_jobs.ProdigiCreateError):
        orders_jobs.create_prodigi_order_job(str(order.id))


def test_create_prodigi_order_job_marks_order_in_production(
    order_setup: dict[str, Any],
    patched_externals: MagicMock,
    db_session: "Session",
) -> None:
    """Successful Prodigi call flips order.status to 'in_production'."""
    order = order_setup["order"]
    orders_jobs.create_prodigi_order_job(str(order.id))
    # Same session — in-memory mutation is visible without a re-fetch.
    assert order.status == "in_production"


def test_create_prodigi_order_job_unknown_order_raises(
    patched_externals: MagicMock,
) -> None:
    with pytest.raises(orders_jobs.OrderNotFoundError):
        orders_jobs.create_prodigi_order_job(str(uuid.uuid4()))
