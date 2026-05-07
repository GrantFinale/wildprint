"""Unit tests for review_app.cart.service.

Hermetic — no Flask app, no Redis, no Stripe. Uses the conftest ``db_session``
fixture (in-memory SQLite with SAVEPOINT rollback per test).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from review_app.cart import service as cart_service
from review_app.cart.models import Cart, CartItem
from review_app.customers.models import Customer
from review_app.prodigi.db_models import ProdigiSku
from review_app.render.db_models import RenderSpecRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def sku(db_session: "Session") -> ProdigiSku:
    """Insert a representative ProdigiSku used across the tests."""
    s = ProdigiSku(
        internal_sku="FP-CLA-16X20-AGOLD",
        prodigi_sku="GLOBAL-CFPM-16X20",
        finish="Antique Gold",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=12900,
        in_stock=True,
    )
    db_session.add(s)
    db_session.flush()
    return s


@pytest.fixture()
def render_spec(db_session: "Session") -> RenderSpecRow:
    """Insert a render_spec row so cart.render_spec_id FKs validate."""
    row = RenderSpecRow(
        spec_hash="a" * 64,
        canonical_inputs={"lake": "test", "species": [], "art_style": "x", "layout_config": {}, "renderer_version": "v1"},
        renderer_version="v1",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def customer(db_session: "Session") -> Customer:
    """Insert a customer for customer-bound cart tests."""
    c = Customer.create(email="buyer@example.com")
    db_session.add(c)
    db_session.flush()
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_add_item_creates_cart_for_anonymous_session(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc123")
    assert cart.id is not None
    assert cart.session_token == "abc123"
    assert cart.customer_id is None

    dto = cart_service.add_item(
        db_session,
        cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=2,
    )
    assert dto.item_count == 2
    assert dto.subtotal_cents == 2 * 12900
    assert len(dto.items) == 1
    assert dto.items[0].unit_price_cents == 12900
    assert dto.items[0].render_spec_id == render_spec.id


def test_add_item_idempotent_for_same_render_spec_and_sku(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    cart_service.add_item(
        db_session, cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=1,
    )
    dto = cart_service.add_item(
        db_session, cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=2,
    )
    # Should collapse to a single line with qty=3, not two lines.
    assert len(dto.items) == 1
    assert dto.items[0].quantity == 3


def test_add_item_validates_quantity_zero(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    with pytest.raises(cart_service.InvalidQuantityError):
        cart_service.add_item(
            db_session, cart,
            prodigi_sku_internal=sku.internal_sku,
            render_spec_id=render_spec.id,
            quantity=0,
        )


def test_add_item_validates_unknown_sku(
    db_session: "Session", render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    with pytest.raises(cart_service.SkuNotFoundError):
        cart_service.add_item(
            db_session, cart,
            prodigi_sku_internal="DOES-NOT-EXIST",
            render_spec_id=render_spec.id,
            quantity=1,
        )


def test_compute_totals_sums_line_totals(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    cart_service.add_item(
        db_session, cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=3,
    )
    totals = cart_service.compute_totals(cart)
    assert totals == {"subtotal_cents": 3 * 12900, "item_count": 3}


def test_update_quantity_zero_removes_line(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    dto = cart_service.add_item(
        db_session, cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=2,
    )
    item_id = dto.items[0].id
    dto2 = cart_service.update_quantity(db_session, cart, item_id=item_id, quantity=0)
    assert dto2.item_count == 0
    assert dto2.items == []


def test_remove_item_drops_line(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow
) -> None:
    cart = cart_service.get_or_create_cart(db_session, session_token="abc")
    dto = cart_service.add_item(
        db_session, cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=1,
    )
    dto2 = cart_service.remove_item(db_session, cart, item_id=dto.items[0].id)
    assert dto2.items == []


def test_merge_anonymous_into_customer_resolves_quantity_conflict(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow,
    customer: Customer,
) -> None:
    """Both carts have the same SKU+spec — last-modified wins."""
    # Anonymous cart with qty=5, will be the *older* one.
    anon = cart_service.get_or_create_cart(db_session, session_token="cookie-tok")
    cart_service.add_item(
        db_session, anon,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=5,
    )

    # Customer cart with qty=2, will be the *newer* one (it touches the row
    # later, after the anonymous side has settled).
    customer_cart = cart_service.get_or_create_cart(
        db_session, customer_id=customer.id
    )
    cart_service.add_item(
        db_session, customer_cart,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=2,
    )

    merged = cart_service.merge_anonymous_into_customer(
        db_session,
        session_token="cookie-tok",
        customer_id=customer.id,
    )
    assert merged is not None
    assert merged.id == customer_cart.id

    dto = cart_service.cart_to_dto(merged)
    # Customer cart's row was touched last → its quantity (2) wins.
    assert len(dto.items) == 1
    assert dto.items[0].quantity == 2


def test_merge_anonymous_into_customer_promotes_when_no_customer_cart(
    db_session: "Session", sku: ProdigiSku, render_spec: RenderSpecRow,
    customer: Customer,
) -> None:
    """Customer has no open cart — anon cart is promoted to the customer."""
    anon = cart_service.get_or_create_cart(db_session, session_token="tok2")
    cart_service.add_item(
        db_session, anon,
        prodigi_sku_internal=sku.internal_sku,
        render_spec_id=render_spec.id,
        quantity=4,
    )
    merged = cart_service.merge_anonymous_into_customer(
        db_session,
        session_token="tok2",
        customer_id=customer.id,
    )
    assert merged is not None
    assert merged.customer_id == customer.id
    assert merged.session_token is None
    assert merged.id == anon.id


def test_merge_anonymous_into_customer_with_no_anon_cart_returns_customer_cart(
    db_session: "Session", customer: Customer,
) -> None:
    merged = cart_service.merge_anonymous_into_customer(
        db_session,
        session_token="never-existed",
        customer_id=customer.id,
    )
    assert merged is not None
    assert merged.customer_id == customer.id
