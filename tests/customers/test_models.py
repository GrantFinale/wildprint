"""Customer + Address model tests.

Covers the partial-unique-on-active-email semantics, soft-delete email reuse,
and the one-default-address-per-customer rule.

Uses the ``db_session`` fixture from ``tests/conftest.py`` which builds an
in-memory SQLite engine via ``Base.metadata.create_all``. We import the
models here so they're registered against ``Base.metadata`` before
``create_all`` runs.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from review_app.addresses.models import Address
from review_app.customers.models import Customer


def test_customer_email_partial_unique_among_active(db_session: Session) -> None:
    """Two active customers with the same email → IntegrityError."""
    a = Customer.create(email="dup@example.com")
    b = Customer.create(email="dup@example.com")
    db_session.add_all([a, b])
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_soft_delete_releases_email_for_reuse(db_session: Session) -> None:
    """A soft-deleted customer's email may be reused by a new active row."""
    soft_deleted = Customer.create(email="reuse@example.com")
    soft_deleted.deleted_at = datetime.now(UTC)
    db_session.add(soft_deleted)
    db_session.flush()

    new = Customer.create(email="reuse@example.com")
    db_session.add(new)
    # Should not raise — partial unique index excludes deleted_at IS NOT NULL.
    db_session.flush()

    fetched = Customer.get_active_by_email(db_session, "reuse@example.com")
    assert fetched is not None
    assert fetched.id == new.id


def test_get_active_by_email_ignores_soft_deleted(db_session: Session) -> None:
    """Lookup by email skips soft-deleted rows."""
    deleted = Customer.create(email="gone@example.com")
    deleted.deleted_at = datetime.now(UTC)
    db_session.add(deleted)
    db_session.flush()

    assert Customer.get_active_by_email(db_session, "gone@example.com") is None
    assert Customer.get_active_by_email(db_session, "GONE@example.com") is None


def test_address_is_default_uniqueness_per_customer(db_session: Session) -> None:
    """A customer can have at most one default shipping address."""
    cust = Customer.create(email="addr@example.com")
    db_session.add(cust)
    db_session.flush()

    a1 = Address(
        customer_id=cust.id,
        line1="123 Main St",
        city="Springfield",
        state="IL",
        zip="62701",
        country="US",
        is_default=True,
    )
    db_session.add(a1)
    db_session.flush()

    a2 = Address(
        customer_id=cust.id,
        line1="456 Side St",
        city="Springfield",
        state="IL",
        zip="62701",
        country="US",
        is_default=True,
    )
    db_session.add(a2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_address_is_default_allows_multiple_non_defaults(
    db_session: Session,
) -> None:
    """Two non-default addresses for the same customer are fine."""
    cust = Customer.create(email="addr2@example.com")
    db_session.add(cust)
    db_session.flush()

    for i in range(3):
        db_session.add(
            Address(
                customer_id=cust.id,
                line1=f"{i} Pine St",
                city="Springfield",
                state="IL",
                zip="62701",
                country="US",
                is_default=False,
            )
        )
    db_session.flush()  # no error
