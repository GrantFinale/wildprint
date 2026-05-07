"""DB-backed content store. Replaces the Phase 4b in-memory dicts.

Surface:
    * :func:`get_block(key)` -> ContentBlock | None (uses a process-bound session)
    * :func:`set_block(session, key, slot, title, body, updated_by_user_id)`
    * :func:`init_app(app)` — pre-populates default blocks on first boot.

The ``content_blocks`` table is keyed by string PK. Slot values:
    * ``marketing`` — the homepage_hero / about_us / faq blocks (Markdown body).
    * ``email`` — email template kinds (HTML body). Key convention:
      ``email.<kind>.subject`` and ``email.<kind>.html``.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from review_app.content.models import ContentBlock

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


EMAIL_TEMPLATE_KINDS: tuple[str, ...] = (
    "order_confirmed",
    "in_production",
    "shipped",
    "delivered",
    "refunded",
    "problem",
)

MARKETING_SLOTS: tuple[str, ...] = ("homepage_hero", "about_us", "faq")


def get_block(key: str, *, session: Session | None = None) -> ContentBlock | None:
    """Fetch a single block by key. Opens its own session if none passed."""
    from review_app.db import get_session_factory

    own = session is None
    s: Session = session or get_session_factory()()
    try:
        return s.get(ContentBlock, key)
    finally:
        if own:
            s.close()


def set_block(
    session: Session,
    *,
    key: str,
    slot: str,
    title: str | None,
    body: str,
    updated_by_user_id: Any | None = None,
) -> ContentBlock:
    """Upsert a content block. Caller commits."""
    now = datetime.now(UTC)
    row = session.get(ContentBlock, key)
    if row is None:
        row = ContentBlock(
            key=key,
            slot=slot,
            title=title,
            body=body,
            updated_by_user_id=updated_by_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.slot = slot
        row.title = title
        row.body = body
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = now
    session.flush()
    return row


def init_app(app: Flask) -> None:
    """On first boot, seed defaults so /admin/content/* renders something.

    Tolerant of missing tables — if the migration hasn't run (e.g. in a
    test that builds a partial schema), the first attempt logs and the
    flag stays unset so the next request will retry. Once the table
    exists, seeding succeeds and the flag flips to True.
    """
    @app.before_request
    def _seed_once_guard() -> None:
        if getattr(app, "_content_seeded", False):
            return
        try:
            seed_defaults()
            app._content_seeded = True  # type: ignore[attr-defined]
        except Exception:
            # Don't crash on first request — just log + leave the flag
            # unset so we can retry once the migration runs.
            _log.debug("content.seed_defaults skipped (table likely missing)")


def seed_defaults() -> None:
    """Idempotent: insert default blocks for any keys not already present."""
    from review_app.db import get_session_factory

    s: Session = get_session_factory()()
    try:
        for slot in MARKETING_SLOTS:
            if get_block(slot, session=s) is None:
                set_block(
                    s,
                    key=slot,
                    slot="marketing",
                    title=slot.replace("_", " ").title(),
                    body=f"Default copy for {slot}. Edit on /admin/content/marketing.",
                )
        for kind in EMAIL_TEMPLATE_KINDS:
            subj_key = f"email.{kind}.subject"
            html_key = f"email.{kind}.html"
            if get_block(subj_key, session=s) is None:
                set_block(
                    s,
                    key=subj_key,
                    slot="email",
                    title=f"{kind} (subject)",
                    body=f"[wildprint] {kind.replace('_', ' ').title()}",
                )
            if get_block(html_key, session=s) is None:
                set_block(
                    s,
                    key=html_key,
                    slot="email",
                    title=f"{kind} (HTML body)",
                    body=(
                        f"<p>Default {kind} email body — edit me on the admin "
                        f"templates page.</p>"
                    ),
                )
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


__all__ = [
    "EMAIL_TEMPLATE_KINDS",
    "MARKETING_SLOTS",
    "ContentBlock",
    "get_block",
    "init_app",
    "seed_defaults",
    "set_block",
]
