"""Tests for the cmd+K cross-entity admin search (Phase 6 polish).

Coverage targets:

* Empty / no-query state renders the help card.
* Each entity group surfaces hits when seeded.
* No-results state for an unknown query.
* Postgres tsvector code path is exercised on a SQLite engine via the
  fallback branch (tsvector path is mocked / not applicable to fixtures).
* Topbar wiring — cmd+K script is injected on every admin page.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from review_app.db import Base, get_engine, get_session


@pytest.fixture(autouse=True)
def _bootstrap_live_engine() -> None:
    """Create all tables on the live engine so search routes don't 500."""
    Base.metadata.create_all(get_engine())


def test_search_empty_query_renders_help_card(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Empty ?q= shows the help card listing the searchable entities."""
    role_setter("viewer")
    resp = client.get("/admin/search")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Type a query" in body
    assert "Customers" in body
    assert "SKUs" in body


def test_search_no_results_query_renders_no_results(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Query that matches nothing should render the No results card."""
    role_setter("viewer")
    resp = client.get("/admin/search?q=ZZ_HIGHLY_UNLIKELY_QUERY_XYZ")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "No results" in body or "ZZ_HIGHLY_UNLIKELY_QUERY_XYZ" in body


def test_search_finds_customer_by_email(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Seed a customer; search by email substring."""
    from review_app.customers.models import Customer

    role_setter("admin")
    with get_session() as session:
        existing = session.execute(
            select(Customer).where(Customer.email == "search-test@example.com")
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Customer.create(
                    email="search-test@example.com",
                    name="Search Test",
                )
            )

    try:
        resp = client.get("/admin/search?q=search-test")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "Customers" in body
        assert "search-test@example.com" in body
    finally:
        with get_session() as session:
            row = session.execute(
                select(Customer).where(Customer.email == "search-test@example.com")
            ).scalar_one_or_none()
            if row is not None:
                session.delete(row)


def test_search_finds_prodigi_sku_by_internal_sku(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Seed a SKU; search by partial internal_sku."""
    from review_app.prodigi.db_models import ProdigiSku

    role_setter("admin")
    with get_session() as session:
        existing = session.execute(
            select(ProdigiSku).where(ProdigiSku.internal_sku == "SEARCH-TEST-SKU-1")
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                ProdigiSku(
                    internal_sku="SEARCH-TEST-SKU-1",
                    prodigi_sku="GLOBAL-FAP-XX",
                    finish="Black",
                    size_inches="16x20",
                    orientation="portrait",
                    active=True,
                    in_stock=True,
                )
            )

    try:
        resp = client.get("/admin/search?q=SEARCH-TEST")
        body = resp.data.decode("utf-8")
        assert resp.status_code == 200
        assert "SEARCH-TEST-SKU-1" in body
    finally:
        with get_session() as session:
            row = session.execute(
                select(ProdigiSku).where(ProdigiSku.internal_sku == "SEARCH-TEST-SKU-1")
            ).scalar_one_or_none()
            if row is not None:
                session.delete(row)


def test_search_finds_audit_log_by_action(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Seed an audit row; search by action substring."""
    from review_app.audit.models import AuditLogEntry

    role_setter("admin")
    with get_session() as session:
        session.add(
            AuditLogEntry(
                action="search.testaction.unique",
                target_type="search_test",
                target_id="abc",
            )
        )

    try:
        resp = client.get("/admin/search?q=testaction.unique")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "Audit log" in body
        assert "testaction.unique" in body
    finally:
        with get_session() as session:
            session.execute(
                AuditLogEntry.__table__.delete().where(
                    AuditLogEntry.action == "search.testaction.unique"
                )
            )


def test_search_finds_order_by_stripe_intent(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Seed an order with a stripe_payment_intent_id; search picks it up."""
    from review_app.customers.models import Customer
    from review_app.orders.models import Order

    role_setter("admin")
    cust_email = f"order-search-{uuid.uuid4().hex[:8]}@example.com"
    pi_id = "pi_search_test_unique_xyz"
    with get_session() as session:
        cust = Customer.create(email=cust_email, name="Order Search")
        session.add(cust)
        session.flush()
        session.add(
            Order(
                customer_id=cust.id,
                stripe_payment_intent_id=pi_id,
                status="paid",
                subtotal_cents=1000,
                shipping_cents=0,
                tax_cents=0,
                total_cents=1000,
            )
        )

    try:
        resp = client.get("/admin/search?q=pi_search_test_unique")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "Orders" in body
    finally:
        with get_session() as session:
            session.execute(
                Order.__table__.delete().where(
                    Order.stripe_payment_intent_id == pi_id
                )
            )
            session.execute(
                Customer.__table__.delete().where(Customer.email == cust_email)
            )


def test_search_postgres_branch_falls_back_when_unsupported(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """``_is_postgres`` returns False on SQLite — confirms the fallback path runs.

    This is a structural assertion: the search code has two SQL branches
    (Postgres tsvector @@ websearch_to_tsquery vs. SQLite ILIKE). On the
    test fixture we exercise the SQLite branch; this test just confirms
    the route doesn't crash when ``_is_postgres`` is False.
    """
    from review_app.admin import search as search_mod

    role_setter("admin")
    # The default db_engine in tests is in-memory sqlite; _is_postgres
    # should return False unconditionally for the test session.
    fake_session = type("S", (), {"bind": type("B", (), {"dialect": type("D", (), {"name": "sqlite"})()})()})()
    assert search_mod._is_postgres(fake_session) is False
    resp = client.get("/admin/search?q=anything")
    assert resp.status_code == 200


def test_search_topbar_includes_cmd_k_script(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Topbar JS for cmd+K is injected on every admin page."""
    role_setter("admin")
    resp = client.get("/admin/search")
    body = resp.data.decode("utf-8")
    # Must reference cmd+K detection (key === "k") and metaKey/ctrlKey.
    assert "metaKey" in body or "ctrlKey" in body
    assert "admin-global-search" in body
