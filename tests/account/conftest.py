"""Account test scaffolding — mirrors tests/checkout/conftest.py.

The /account blueprint opens its own session via review_app.db.get_session_factory(),
which won't see writes inside the global db_session SAVEPOINT fixture. Use a
per-test in-memory engine stamped into review_app.db so blueprint code +
tests share one view of the world.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


@pytest.fixture()
def account_engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )

    # Side-effect imports — register every model module on Base.metadata.
    import review_app.account.models
    import review_app.addresses.models
    import review_app.ai.models
    import review_app.audit.models
    import review_app.auth.models
    import review_app.cart.models
    import review_app.checkout.stripe_events
    import review_app.content.models
    import review_app.customers.models
    import review_app.email.outbox
    import review_app.notes.models
    import review_app.orders.models
    import review_app.prodigi.db_models
    import review_app.refunds.models
    import review_app.refunds.reprints_models
    import review_app.render.db_models
    from review_app.db import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def account_app(account_engine: Engine) -> Iterator[Flask]:
    from flask import Flask
    from sqlalchemy.orm import sessionmaker

    import review_app.db as db_mod
    from review_app.account import init_app as init_account

    saved_engine = db_mod._engine
    saved_factory = db_mod._session_factory

    db_mod._engine = account_engine
    db_mod._session_factory = sessionmaker(
        bind=account_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )

    app = Flask(
        __name__,
        template_folder="../../review_app/templates",
        static_folder="../../review_app/static",
    )
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    init_account(app)
    yield app

    db_mod._engine = saved_engine
    db_mod._session_factory = saved_factory


@pytest.fixture()
def account_client(account_app: Flask) -> FlaskClient:
    return account_app.test_client()


@pytest.fixture()
def account_db_session(account_engine: Engine) -> Iterator[Session]:
    """Session bound to the per-test engine. Caller must commit."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(
        bind=account_engine, expire_on_commit=False, future=True
    )
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
