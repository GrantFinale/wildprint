"""Shared fixtures for the Phase 4b admin test suite.

The Phase 4a parallel agent owns the top-level ``admin_bp``; until they
land, we wire our own ``admin_bp`` (Blueprint name="admin", url_prefix=
"/admin") and register only the Phase 4b sub-modules onto it. This makes
each admin module independently testable without booting the full app.

Each test gets a fresh Flask app + bound DB session. The DB session is
the standard SAVEPOINT-rolled-back one from the top-level conftest, but we
also patch ``review_app.admin._session.get_session`` to return that same
session so the routes use it.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Iterator

import pytest

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy.orm import Session


@pytest.fixture()
def admin_app(db_session: Session) -> Iterator[Flask]:
    """Build a minimal Flask app with the Phase 4b ``admin_bp`` mounted.

    The app shares the test's ``db_session`` so ORM writes show up in
    queries inside the route handlers.
    """
    from flask import Blueprint, Flask

    from review_app.admin._phase4b import register_phase4b_routes

    template_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "review_app", "templates"
        )
    )
    app = Flask("wildprint_admin_test", template_folder=template_dir)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        WTF_CSRF_ENABLED=False,
    )

    admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

    # Stub the few endpoints other modules link to but that this Phase
    # 4b code doesn't own (parallel agent's pages).
    @admin_bp.route("/dashboard", endpoint="dashboard")
    def _dashboard() -> str:
        return "stub"

    @admin_bp.route("/search", endpoint="search")
    def _search() -> str:
        return "stub"

    register_phase4b_routes(admin_bp)
    app.register_blueprint(admin_bp)

    # Stub auth blueprint (real auth blueprint owns these endpoints).
    auth_bp = Blueprint("auth", __name__, url_prefix="/admin")

    @auth_bp.route("/login", endpoint="login")
    def _login() -> str:
        return "stub"

    @auth_bp.route("/logout", endpoint="logout")
    def _logout() -> str:
        return "stub"

    app.register_blueprint(auth_bp)

    # The parallel agent's _base.html relies on a global context. Provide
    # the same shape so admin templates render in tests without booting
    # the full app.
    @app.context_processor
    def _shell_context() -> dict[str, Any]:
        class _AnonUser:
            is_authenticated = False
            email = ""
            role = ""

        # Empty nav in tests — the real shell context processor (owned by
        # the parallel agent) populates this from NAV_TREE filtered by
        # current_role. Tests assert page CONTENT, not sidebar links.
        return {
            "nav_categories": [],
            "current_role": "admin",
            "prodigi_env_pill": {
                "label": "SANDBOX",
                "css_class": "env-pill env-pill--sandbox",
            },
            "admin_notifications": 0,
            "build_id": "",
            "current_user": _AnonUser(),
            "breadcrumbs": [],
            "page_title": "Admin",
        }

    # Patch the session helper to use the test's transactional session
    # so writes persist within the test and roll back after.
    import review_app.admin._session as session_helper

    original = session_helper.get_session
    session_helper.get_session = lambda: db_session  # type: ignore[assignment]
    try:
        yield app
    finally:
        session_helper.get_session = original


@pytest.fixture()
def admin_client(admin_app: Flask) -> FlaskClient:
    return admin_app.test_client()


@pytest.fixture()
def role_setter(
    monkeypatch: pytest.MonkeyPatch,
) -> "Callable[[str | None], None]":
    """Phase 4a fixture — swap the role observed by sidebar/templates.

    The shell tests (Phase 4a) need to assert role-gated visibility
    against the REAL ``app`` fixture (the full review_app). This fixture
    flips ``ADMIN_AUTH_ENABLED`` on, monkey-patches the
    ``current_user`` binding the @requires_role decorator captured at
    import time, and replaces ``current_role()`` so the Jinja sidebar
    sees the chosen role.

    Usage::

        def test_x(client, role_setter):
            role_setter("staff")
            client.get("/admin")  # behaves as if logged in as staff
    """
    from collections.abc import Callable

    def setter(role: str | None) -> None:
        if role is None:
            monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
        else:
            monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
        monkeypatch.setattr(
            "review_app.admin._helpers.current_role", lambda: role
        )

        class _StandIn:
            is_authenticated: bool = role is not None
            is_anonymous: bool = role is None
            email: str = "test@example.com"

            def __init__(self, role: str | None) -> None:
                self.role = role

            def get_id(self) -> str:
                return "test-id"

        from review_app.auth import decorators as _dec

        monkeypatch.setattr(_dec, "current_user", _StandIn(role))
        # Also patch where templates / topbar look up current_user.
        try:
            import flask_login

            monkeypatch.setattr(flask_login, "current_user", _StandIn(role))
        except ImportError:
            pass

    return setter


@pytest.fixture()
def seed_minimal_catalog(db_session: Session) -> dict[str, Any]:
    """Seed one ProdigiSku so test-order forms have a pickable option."""
    from review_app.prodigi.db_models import ProdigiSku

    sku = ProdigiSku(
        internal_sku="POSTER-16X20-BLACK",
        prodigi_sku="GLOBAL-FAP-16X20",
        finish="Black",
        size_inches="16x20",
        orientation="portrait",
        active=True,
        retail_price_cents=4900,
        in_stock=True,
    )
    db_session.add(sku)
    db_session.flush()
    return {"sku": sku}
