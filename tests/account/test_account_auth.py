"""Magic-link auth tests for /account/login flow."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from review_app.account.auth import issue_token, verify_token
from review_app.account.models import CustomerLoginToken
from review_app.customers.models import Customer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


SECRET = "test-secret-key-do-not-use-in-prod"


@pytest.fixture()
def customer(db_session: Session) -> Customer:
    c = Customer.create(email="alice@example.com", name="Alice")
    db_session.add(c)
    db_session.flush()
    return c


def test_issue_then_verify_returns_customer_id(
    db_session: Session, customer: Customer
) -> None:
    token = issue_token(db_session, customer_id=customer.id, secret_key=SECRET)
    assert isinstance(token, str) and len(token) > 20
    cid = verify_token(db_session, token=token, secret_key=SECRET)
    assert cid == customer.id


def test_verify_marks_token_used(db_session: Session, customer: Customer) -> None:
    token = issue_token(db_session, customer_id=customer.id, secret_key=SECRET)
    verify_token(db_session, token=token, secret_key=SECRET)
    rows = db_session.query(CustomerLoginToken).all()
    assert len(rows) == 1
    assert rows[0].used_at is not None


def test_used_token_cannot_verify_twice(
    db_session: Session, customer: Customer
) -> None:
    token = issue_token(db_session, customer_id=customer.id, secret_key=SECRET)
    assert verify_token(db_session, token=token, secret_key=SECRET) is not None
    assert verify_token(db_session, token=token, secret_key=SECRET) is None


def test_expired_token_rejected(db_session: Session, customer: Customer) -> None:
    token = issue_token(db_session, customer_id=customer.id, secret_key=SECRET)
    row = db_session.query(CustomerLoginToken).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    assert verify_token(db_session, token=token, secret_key=SECRET) is None


def test_bad_signature_rejected(
    db_session: Session, customer: Customer
) -> None:
    token = issue_token(db_session, customer_id=customer.id, secret_key=SECRET)
    assert verify_token(db_session, token=token, secret_key="wrong-secret") is None


def test_garbage_token_rejected(db_session: Session) -> None:
    assert verify_token(db_session, token="not-a-jwt", secret_key=SECRET) is None
