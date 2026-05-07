"""End-to-end-ish tests for /account/* routes via the account_client fixture.

Uses tests/account/conftest.py which builds a per-test in-memory engine and
stamps it into review_app.db so blueprint sessions and test sessions share
one view.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from review_app.customers.models import Customer

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


@pytest.fixture()
def signed_in(
    account_app: Flask,
    account_client: FlaskClient,
    account_db_session: Session,
) -> tuple[FlaskClient, Customer]:
    """Create a customer + bypass magic-link by setting session['customer_id']."""
    cust = Customer.create(email="bob@example.com", name="Bob")
    account_db_session.add(cust)
    account_db_session.commit()

    with account_client.session_transaction() as sess:
        sess["customer_id"] = str(cust.id)
    return account_client, cust


def test_login_get_renders(account_client: FlaskClient) -> None:
    resp = account_client.get("/account/login")
    assert resp.status_code == 200
    assert b"Sign in" in resp.data


def test_orders_list_redirects_when_anonymous(
    account_client: FlaskClient,
) -> None:
    resp = account_client.get("/account/orders")
    assert resp.status_code == 302
    assert "/account/login" in resp.headers["Location"]


def test_overview_redirects_when_anonymous(
    account_client: FlaskClient,
) -> None:
    resp = account_client.get("/account/")
    assert resp.status_code == 302


def test_overview_renders_for_signed_in_customer(
    signed_in: tuple[FlaskClient, Customer],
) -> None:
    cli, cust = signed_in
    resp = cli.get("/account/")
    assert resp.status_code == 200
    assert cust.email.encode() in resp.data


def test_orders_list_renders(signed_in: tuple[FlaskClient, Customer]) -> None:
    cli, _ = signed_in
    resp = cli.get("/account/orders")
    assert resp.status_code == 200


def test_addresses_list_renders(
    signed_in: tuple[FlaskClient, Customer],
) -> None:
    cli, _ = signed_in
    resp = cli.get("/account/addresses")
    assert resp.status_code == 200


def test_profile_renders(signed_in: tuple[FlaskClient, Customer]) -> None:
    cli, cust = signed_in
    resp = cli.get("/account/profile")
    assert resp.status_code == 200
    assert cust.email.encode() in resp.data


def test_logout_clears_session(
    signed_in: tuple[FlaskClient, Customer],
) -> None:
    cli, _ = signed_in
    resp = cli.get("/account/logout")
    assert resp.status_code == 302
    resp2 = cli.get("/account/")
    assert resp2.status_code == 302
