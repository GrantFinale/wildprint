"""Admin notes — short text annotations on orders or customers.

Surface:
    * :func:`add(session, target_type, target_id, body, author_user_id)`
    * :func:`list_for(session, target_type, target_id)`
    * :func:`init_app(app)` — no-op (registered for symmetry).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from review_app.notes.models import Note

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.orm import Session


VALID_NOTE_TARGETS: frozenset[str] = frozenset({"order", "customer"})


def add(
    session: Session,
    *,
    target_type: str,
    target_id: uuid.UUID,
    body: str,
    author_user_id: uuid.UUID,
) -> Note:
    """Insert a note. Caller commits."""
    if target_type not in VALID_NOTE_TARGETS:
        raise ValueError(f"invalid target_type: {target_type!r}")
    cleaned = (body or "").strip()
    if not cleaned:
        raise ValueError("body cannot be empty")
    now = datetime.now(UTC)
    note = Note(
        target_type=target_type,
        target_id=target_id,
        body=cleaned,
        author_user_id=author_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(note)
    session.flush()
    return note


def list_for(
    session: Session,
    *,
    target_type: str,
    target_id: uuid.UUID,
) -> list[Note]:
    """Return non-deleted notes for a target, newest-first."""
    if target_type not in VALID_NOTE_TARGETS:
        return []
    rows = list(
        session.execute(
            select(Note)
            .where(Note.target_type == target_type)
            .where(Note.target_id == target_id)
            .where(Note.deleted_at.is_(None))
            .order_by(Note.created_at.desc())
        )
        .scalars()
        .all()
    )
    return rows


def init_app(app: Flask) -> None:
    """No-op wiring stub — included for parity with other modules."""
    return None


__all__ = ["VALID_NOTE_TARGETS", "Note", "add", "init_app", "list_for"]
