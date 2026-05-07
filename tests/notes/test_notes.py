"""Tests for review_app.notes."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from review_app import notes as notes_mod

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _author(db_session: Session) -> uuid.UUID:
    """Create a User row to satisfy the FK and return its id."""
    from review_app.auth.models import User

    u = User(
        email="admin@fishingposter.com",
        password_hash="argon2$dummy",
        role="admin",
    )
    db_session.add(u)
    db_session.flush()
    return u.id


def test_add_and_list_notes(db_session: Session) -> None:
    target = uuid.uuid4()
    author = _author(db_session)
    notes_mod.add(
        db_session,
        target_type="order",
        target_id=target,
        body="first note",
        author_user_id=author,
    )
    notes_mod.add(
        db_session,
        target_type="order",
        target_id=target,
        body="second note",
        author_user_id=author,
    )
    rows = notes_mod.list_for(db_session, target_type="order", target_id=target)
    assert len(rows) == 2
    # Newest-first.
    assert rows[0].body == "second note"


def test_notes_scoped_by_target(db_session: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    author = _author(db_session)
    notes_mod.add(
        db_session,
        target_type="order",
        target_id=a,
        body="for a",
        author_user_id=author,
    )
    notes_mod.add(
        db_session,
        target_type="order",
        target_id=b,
        body="for b",
        author_user_id=author,
    )
    a_rows = notes_mod.list_for(db_session, target_type="order", target_id=a)
    b_rows = notes_mod.list_for(db_session, target_type="order", target_id=b)
    assert {r.body for r in a_rows} == {"for a"}
    assert {r.body for r in b_rows} == {"for b"}


def test_invalid_target_type_raises(db_session: Session) -> None:
    author = _author(db_session)
    with pytest.raises(ValueError):
        notes_mod.add(
            db_session,
            target_type="bogus",
            target_id=uuid.uuid4(),
            body="x",
            author_user_id=author,
        )


def test_empty_body_rejected(db_session: Session) -> None:
    author = _author(db_session)
    with pytest.raises(ValueError):
        notes_mod.add(
            db_session,
            target_type="customer",
            target_id=uuid.uuid4(),
            body="   ",
            author_user_id=author,
        )
