"""Per-request DB session helpers shared across Phase 4b admin views.

Mirrors the ``_get_session`` / ``_close_session_if_owned`` pattern used by
:mod:`review_app.checkout.routes` and :mod:`review_app.cart.routes` so admin
pages don't reinvent it. Each request that hits an admin view gets a single
SQLAlchemy session attached to ``flask.g``; it is committed on success and
closed in a teardown handler.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_session() -> Session:
    """Return the request-bound SQLAlchemy session, creating one if needed."""
    from flask import g

    existing = getattr(g, "db", None)
    if existing is not None:
        return existing  # type: ignore[no-any-return]

    from review_app.db import get_session_factory

    session = get_session_factory()()
    g.db = session
    g.db_owned_by_request = True
    return session


def close_session_if_owned(session: Session, commit: bool) -> None:
    """Commit/rollback + close the session iff this request owns it."""
    from flask import g

    if not getattr(g, "db_owned_by_request", False):
        return
    try:
        if commit:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()
        g.db = None
        g.db_owned_by_request = False


__all__ = ["close_session_if_owned", "get_session"]
