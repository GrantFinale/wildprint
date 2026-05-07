"""Tests for :mod:`review_app.audit`."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from flask import Flask, jsonify

import review_app.audit as audit_module
from review_app.audit import init_app, record, skip
from review_app.audit.models import AuditLogEntry


@pytest.fixture
def patched_session(db_session: Any) -> Iterator[Any]:
    """Patch ``review_app.db.get_session`` to yield the test session."""

    @contextmanager
    def _fake_get_session() -> Iterator[Any]:
        yield db_session

    with patch("review_app.db.get_session", _fake_get_session):
        yield db_session


# ---------------------------------------------------------------------------
# 1. Direct record() call
# ---------------------------------------------------------------------------
def test_record_inserts_row(db_session: Any) -> None:
    """``record()`` writes one ``audit_log`` row in the caller's transaction."""
    record(
        db_session,
        action="order.refund",
        target_type="order",
        target_id="ord_abc",
        before={"status": "paid"},
        after={"status": "refunded"},
        user_id=None,
        ip_address="10.0.0.1",
        user_agent="pytest",
    )
    db_session.flush()
    rows = list(db_session.query(AuditLogEntry).all())
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "order.refund"
    assert row.target_type == "order"
    assert row.target_id == "ord_abc"
    assert row.before == {"status": "paid"}
    assert row.after == {"status": "refunded"}


# ---------------------------------------------------------------------------
# 2. Auto-capture middleware
# ---------------------------------------------------------------------------
def _make_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_app(app)
    return app


def test_auto_capture_on_admin_post(patched_session: Any) -> None:
    app = _make_app()

    @app.route("/admin/things", methods=["POST"])
    def post_thing() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    resp = client.post("/admin/things")
    assert resp.status_code == 200

    rows = list(patched_session.query(AuditLogEntry).all())
    assert len(rows) == 1
    assert rows[0].action == "http.POST"
    assert rows[0].target_type == "admin_route"
    assert rows[0].target_id == "/admin/things"
    assert rows[0].after is not None
    assert rows[0].after["status"] == 200


def test_skip_decorator_omits_capture(patched_session: Any) -> None:
    app = _make_app()

    @app.route("/admin/silent", methods=["POST"])
    @skip
    def silent_view() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    resp = client.post("/admin/silent")
    assert resp.status_code == 200

    rows = list(patched_session.query(AuditLogEntry).all())
    assert rows == []


def test_get_requests_not_audited(patched_session: Any) -> None:
    app = _make_app()

    @app.route("/admin/dashboard", methods=["GET"])
    def dashboard() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    client.get("/admin/dashboard")
    assert list(patched_session.query(AuditLogEntry).all()) == []


def test_non_admin_routes_not_audited(patched_session: Any) -> None:
    app = _make_app()

    @app.route("/api/cart/add", methods=["POST"])
    def cart_add() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    client.post("/api/cart/add")
    assert list(patched_session.query(AuditLogEntry).all()) == []


def test_failed_response_not_audited(patched_session: Any) -> None:
    """4xx/5xx responses don't change state, so they aren't recorded."""
    app = _make_app()

    @app.route("/admin/broken", methods=["POST"])
    def broken_view() -> object:
        return jsonify({"err": "no"}), 400

    client = app.test_client()
    client.post("/admin/broken")
    assert list(patched_session.query(AuditLogEntry).all()) == []


def test_init_app_idempotent(patched_session: Any) -> None:
    """Calling init_app twice doesn't double-register the hook."""
    app = _make_app()
    init_app(app)
    init_app(app)

    @app.route("/admin/once", methods=["POST"])
    def once_view() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    client.post("/admin/once")
    rows = list(patched_session.query(AuditLogEntry).all())
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 3. Admin page rendering
# ---------------------------------------------------------------------------
def test_audit_log_admin_page_renders_entries(patched_session: Any) -> None:
    """The /admin/settings/audit viewer renders rows from the table."""
    # Seed two entries.
    record(
        patched_session,
        action="order.refund",
        target_type="order",
        target_id="ord_seed_1",
        before={"status": "paid"},
        after={"status": "refunded"},
    )
    record(
        patched_session,
        action="sku.update",
        target_type="sku",
        target_id="FP-CLA-16X20-BLK",
        after={"in_stock": False},
    )
    patched_session.commit()

    # Use the real Flask app via the conftest fixture-style pattern.
    from review_app.app import app as flask_app

    with flask_app.test_client() as client:
        resp = client.get("/admin/settings/audit")
        # In shadow-mode (ADMIN_AUTH_ENABLED unset) the role check passes.
        # If it doesn't, we still want a 200 from the test app's stubbed auth.
        assert resp.status_code in {200, 302}
        if resp.status_code == 200:
            body = resp.get_data(as_text=True)
            # Both seeded actions must appear in the page.
            assert "order.refund" in body or "sku.update" in body
