"""Phase 4b — Content admin route tests."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


def test_email_templates_admin_only(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """The email templates page renders all 6 known kinds.

    Role gating is shadow-mode in tests (passthrough) — we assert the
    route reaches the rendered page rather than the auth gate.
    """
    resp = admin_client.get("/admin/content/email-templates")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for kind in (
        "order_confirmed",
        "in_production",
        "shipped",
        "delivered",
        "refunded",
        "problem",
    ):
        assert kind in body, f"missing template kind: {kind}"


def test_email_log_filters_by_template(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """The log filters list to a chosen template kind."""
    from review_app.email.outbox import enqueue

    enqueue(
        db_session, kind="email.order_confirmed", to="a@x.com",
        payload={"subject": "hi a"},
    )
    enqueue(
        db_session, kind="email.shipped", to="b@x.com",
        payload={"subject": "hi b"},
    )
    db_session.flush()

    resp = admin_client.get("/admin/content/email-log?template=shipped")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "b@x.com" in body
    assert "a@x.com" not in body


def test_send_test_email_enqueues_outbox(
    admin_client: FlaskClient, db_session: Session
) -> None:
    """POST action=test_send writes a row to outbox."""
    from sqlalchemy import select

    from review_app.email.outbox import OutboxEntry

    resp = admin_client.post(
        "/admin/content/email-templates",
        data={
            "action": "test_send",
            "kind": "shipped",
            "recipient": "qa@example.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    rows = list(
        db_session.execute(
            select(OutboxEntry).where(OutboxEntry.kind == "email.shipped")
        )
        .scalars()
        .all()
    )
    assert any(
        (r.payload or {}).get("to") == "qa@example.com" for r in rows
    )
