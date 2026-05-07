"""Test scaffolding shared by tests/checkout/.

Strategy
--------
We can't use the global ``db_session`` SAVEPOINT-rollback fixture here because
the blueprint code opens its OWN session via ``review_app.db.get_session_factory()``.
That session lives in a different transaction; rollback on the test session
won't undo what the blueprint committed.

Instead, this conftest:

1. Builds a fresh in-memory SQLite engine per **test** (function scope), so
   data never leaks between tests.
2. Stamps that engine into the lazy ``review_app.db`` module so blueprint
   code uses it.
3. Exposes a ``checkout_db_session`` fixture pointing at the same engine so
   tests can read/seed data with the same view as the blueprint.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


@pytest.fixture()
def checkout_engine() -> Iterator[Engine]:
    """Per-test in-memory SQLite engine + create_all of every model module."""
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )

    import review_app.addresses.models
    import review_app.ai.models

    # Side-effect imports — register every model module.
    import review_app.auth.models
    import review_app.cart.models
    import review_app.checkout.stripe_events
    import review_app.customers.models
    import review_app.email.outbox
    import review_app.orders.models
    import review_app.prodigi.db_models
    import review_app.refunds.models
    import review_app.render.db_models
    from review_app.db import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def checkout_app(checkout_engine: Engine) -> Iterator[Flask]:
    """Build a minimal Flask app with only the cart + checkout blueprints."""
    from flask import Flask
    from sqlalchemy.orm import sessionmaker

    import review_app.db as db_mod
    from review_app.cart import init_app as init_cart
    from review_app.checkout import init_app as init_checkout

    saved_engine = db_mod._engine
    saved_factory = db_mod._session_factory

    db_mod._engine = checkout_engine
    db_mod._session_factory = sessionmaker(
        bind=checkout_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )

    app = Flask(__name__, template_folder="../../review_app/templates")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    init_cart(app)
    init_checkout(app)
    yield app

    db_mod._engine = saved_engine
    db_mod._session_factory = saved_factory


@pytest.fixture()
def checkout_client(checkout_app: Flask) -> FlaskClient:
    return checkout_app.test_client()


@pytest.fixture()
def checkout_db_session(checkout_engine: Engine) -> Iterator[Session]:
    """A simple sessionmaker-backed session bound to the per-test engine.

    No SAVEPOINT rollback — the engine itself is per-test, so nothing leaks.
    Tests are responsible for committing if they want their writes visible
    to the blueprint's separate sessions.
    """
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=checkout_engine, expire_on_commit=False, future=True)
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def populated_db(checkout_db_session: Session) -> dict[str, Any]:
    """Populate the test DB with a cart, sku, render_spec, customer, address."""
    from datetime import UTC, datetime

    from review_app.addresses.models import Address
    from review_app.cart.models import Cart, CartItem
    from review_app.customers.models import Customer
    from review_app.prodigi.db_models import ProdigiSku
    from review_app.render.db_models import RenderSpecRow

    sku = ProdigiSku(
        internal_sku="FP-CLA-16X20-AGOLD",
        prodigi_sku="GLOBAL-CFPM-16X20",
        finish="Antique Gold",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=12900,
        in_stock=True,
    )
    spec = RenderSpecRow(
        spec_hash="b" * 64,
        canonical_inputs={
            "lake": "Test Lake",
            "species": [],
            "art_style": "x",
            "layout_config": {},
            "renderer_version": "v1",
        },
        renderer_version="v1",
    )
    cust = Customer.create(email="buyer@example.com")
    checkout_db_session.add_all([sku, spec, cust])
    checkout_db_session.flush()

    addr = Address(
        customer_id=cust.id,
        line1="123 Main St",
        city="Portland",
        state="OR",
        zip="97201",
        country="US",
        validated_at=datetime.now(UTC),
        validation_provider="smarty",
        dpv_match_code="Y",
    )
    checkout_db_session.add(addr)
    checkout_db_session.flush()

    cart = Cart(customer_id=cust.id, status="open")
    checkout_db_session.add(cart)
    checkout_db_session.flush()

    item = CartItem(
        cart_id=cart.id,
        render_spec_id=spec.id,
        prodigi_sku_internal=sku.internal_sku,
        quantity=1,
        unit_price_cents=sku.retail_price_cents or 0,
    )
    checkout_db_session.add(item)
    checkout_db_session.flush()
    checkout_db_session.commit()

    return {
        "sku": sku,
        "spec": spec,
        "customer": cust,
        "address": addr,
        "cart": cart,
        "item": item,
    }
