"""Tests for the DB-backed content_blocks store."""
from __future__ import annotations

from typing import TYPE_CHECKING

from review_app.content import get_block, set_block
from review_app.content.models import ContentBlock

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_get_block_returns_none_for_unset(db_session: Session) -> None:
    assert get_block("does.not.exist", session=db_session) is None


def test_set_block_inserts(db_session: Session) -> None:
    set_block(
        db_session,
        key="homepage_hero",
        slot="marketing",
        title="Homepage Hero",
        body="Catch your dinner. Hang it on the wall.",
    )
    db_session.commit()
    block = get_block("homepage_hero", session=db_session)
    assert block is not None
    assert block.slot == "marketing"
    assert "dinner" in block.body


def test_set_block_overwrites(db_session: Session) -> None:
    set_block(
        db_session,
        key="about_us",
        slot="marketing",
        title="About",
        body="v1",
    )
    db_session.commit()
    set_block(
        db_session,
        key="about_us",
        slot="marketing",
        title="About",
        body="v2",
    )
    db_session.commit()
    rows = db_session.query(ContentBlock).filter_by(key="about_us").all()
    assert len(rows) == 1
    assert rows[0].body == "v2"


def test_set_block_email_template(db_session: Session) -> None:
    set_block(
        db_session,
        key="email.shipped.subject",
        slot="email",
        title="shipped subject",
        body="Your order shipped!",
    )
    db_session.commit()
    block = get_block("email.shipped.subject", session=db_session)
    assert block is not None
    assert block.slot == "email"
