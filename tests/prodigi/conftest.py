"""Shared fixtures for the prodigi test suite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def prodigi_engine() -> Iterator[Engine]:
    """A dedicated SQLite engine for prodigi tests using StaticPool.

    The default conftest's ``db_engine`` opens fresh connections per
    checkout, which against ``sqlite:///:memory:`` means every checkout
    sees a different database. The webhook code path opens its own
    session via ``get_session_factory()()`` and commits writes that
    a verification query (using a different connection) wouldn't see.
    StaticPool keeps all access on a single shared connection so writes
    are visible across the test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from review_app.db.base import Base

    # Make sure the prodigi models are registered on Base.metadata.
    from review_app.prodigi import db_models  # noqa: F401

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_prodigi_tables(prodigi_engine: Engine) -> Iterator[None]:
    """Wipe the prodigi tables before each test for isolation.

    Runs *before* the test (via the pre-yield branch) so a single test
    starts with empty tables even if a prior test left rows behind.
    """
    from sqlalchemy import text

    with prodigi_engine.begin() as conn:
        for table in (
            "prodigi_callbacks",
            "shipments",
            "prodigi_orders",
            "prodigi_skus",
        ):
            try:
                conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass
    yield


@pytest.fixture(autouse=True)
def _bind_global_session_factory(prodigi_engine: Engine) -> Iterator[None]:
    """Bind ``review_app.db.get_session_factory()`` at the prodigi engine.

    Webhook + quote-refresh code paths use ``get_session_factory()()`` to
    open ad-hoc sessions; without this fixture they'd open against
    ``DATABASE_URL`` (or the lazy ``sqlite:///dev.db`` fallback) and
    bypass our test data entirely.
    """
    from sqlalchemy.orm import sessionmaker

    from review_app import db as db_module

    original_engine = db_module._engine  # type: ignore[attr-defined]
    original_factory = db_module._session_factory  # type: ignore[attr-defined]
    original_scoped = db_module._scoped_session  # type: ignore[attr-defined]

    db_module._engine = prodigi_engine  # type: ignore[attr-defined]
    db_module._session_factory = sessionmaker(  # type: ignore[attr-defined]
        bind=prodigi_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    db_module._scoped_session = None  # type: ignore[attr-defined]
    try:
        yield
    finally:
        db_module._engine = original_engine  # type: ignore[attr-defined]
        db_module._session_factory = original_factory  # type: ignore[attr-defined]
        db_module._scoped_session = original_scoped  # type: ignore[attr-defined]


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture by filename (e.g. 'order_response_created.json')."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture()
def order_created_fixture() -> dict[str, Any]:
    return load_fixture("order_response_created.json")


@pytest.fixture()
def order_complete_fixture() -> dict[str, Any]:
    return load_fixture("order_response_complete.json")


@pytest.fixture()
def quote_fixture() -> dict[str, Any]:
    return load_fixture("quote_response.json")


@pytest.fixture()
def product_fixture() -> dict[str, Any]:
    return load_fixture("product_response.json")


@pytest.fixture()
def callback_inprogress_fixture() -> dict[str, Any]:
    return load_fixture("callback_inprogress.json")


@pytest.fixture()
def callback_shipment_fixture() -> dict[str, Any]:
    return load_fixture("callback_shipment.json")
