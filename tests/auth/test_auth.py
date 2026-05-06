"""Unit tests for the admin auth module.

Strategy:
    - Self-contained Flask app per test (no dependency on review_app/app.py;
      that module is intentionally untouched until the wiring pass).
    - In-memory SQLite via DATABASE_URL=sqlite:///:memory: — fast, hermetic.
    - Every test toggles ADMIN_AUTH_ENABLED explicitly via monkeypatch so we
      can verify both shadow-mode and enforced-mode behavior.

Postgres-specific behavior (CITEXT, CREATE EXTENSION) is exercised by the
integration tests under @pytest.mark.integration; those run only when
DATABASE_URL is set to a Postgres URL and are skipped otherwise.
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Force a fresh in-memory DB BEFORE importing review_app.db (which caches the
# engine on first access via its lazy __getattr__).
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-do-not-use-in-prod")
# Disable WTF CSRF inside tests so we don't need to scrape tokens. CSRF is
# still wired in production via auth.csrf — exercised in integration tests.
os.environ.setdefault("WTF_CSRF_ENABLED", "false")

import review_app.db as db_module  # noqa: E402
from review_app.auth import init_app as init_auth  # noqa: E402
from review_app.auth.decorators import requires_role  # noqa: E402
from review_app.auth.models import User  # noqa: E402
from review_app.auth.routes import auth_bp  # noqa: E402
from review_app.db.base import Base  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine per test. Tables created from metadata."""
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    # Create tables directly from metadata — bypasses Alembic for unit speed.
    # Integration tests cover the actual migration.
    Base.metadata.create_all(eng)

    # Patch the cached singletons inside review_app.db so app code uses our
    # ephemeral engine instead of whatever DATABASE_URL pointed at on import.
    db_module._engine = eng
    db_module._session_factory = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False, future=True
    )
    db_module._scoped_session = None

    yield eng

    Base.metadata.drop_all(eng)
    db_module._engine = None
    db_module._session_factory = None
    db_module._scoped_session = None


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    """Per-test session; rolls back on teardown."""
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app(engine, monkeypatch) -> Flask:
    """Minimal Flask app with auth wired and one role-gated test route."""
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")

    flask_app = Flask(__name__, template_folder="../../review_app/templates")
    flask_app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["TESTING"] = True

    init_auth(flask_app)
    flask_app.register_blueprint(auth_bp)

    @flask_app.route("/admin/protected")
    @requires_role("admin")
    def protected():
        return "ok-admin"

    @flask_app.route("/admin/staff-only")
    @requires_role("staff")
    def staff_only():
        return "ok-staff"

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_user(db_session) -> User:
    user = User.create(email="admin@example.com", password="correct-horse-battery-staple", role="admin")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def viewer_user(db_session) -> User:
    user = User.create(email="viewer@example.com", password="correct-horse-battery-staple", role="viewer")
    db_session.add(user)
    db_session.commit()
    return user


def _login(client, email: str, password: str):
    return client.post(
        "/admin/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Decorator behavior
# ---------------------------------------------------------------------------
class TestRequiresRole:
    def test_anonymous_gets_401_redirect(self, client, admin_user):
        resp = client.get("/admin/protected")
        assert resp.status_code == 401
        assert "/admin/login" in resp.headers.get("Location", "")

    def test_wrong_role_gets_403(self, client, viewer_user):
        login_resp = _login(client, "viewer@example.com", "correct-horse-battery-staple")
        assert login_resp.status_code in (302, 200)
        resp = client.get("/admin/protected")
        assert resp.status_code == 403

    def test_correct_role_gets_200(self, client, admin_user):
        login_resp = _login(client, "admin@example.com", "correct-horse-battery-staple")
        assert login_resp.status_code in (302, 200)
        resp = client.get("/admin/protected")
        assert resp.status_code == 200
        assert resp.data == b"ok-admin"


# ---------------------------------------------------------------------------
# Login / logout flow
# ---------------------------------------------------------------------------
class TestLoginFlow:
    def test_bad_password_returns_401_no_session(self, client, admin_user):
        resp = _login(client, "admin@example.com", "wrong-password")
        assert resp.status_code == 401
        # Session cookie was not set with a logged-in user.
        protected = client.get("/admin/protected")
        assert protected.status_code == 401

    def test_unknown_email_returns_401(self, client):
        resp = _login(client, "nobody@example.com", "any-password-here")
        assert resp.status_code == 401

    def test_good_password_sets_session_and_updates_last_login(
        self, client, admin_user, db_session
    ):
        before = admin_user.last_login_at
        resp = _login(client, "admin@example.com", "correct-horse-battery-staple")
        assert resp.status_code in (302, 200)
        # Subsequent request reaches a protected route.
        protected = client.get("/admin/protected")
        assert protected.status_code == 200

        # last_login_at was bumped.
        db_session.expire_all()
        refreshed = db_session.get(User, admin_user.id)
        assert refreshed is not None
        assert refreshed.last_login_at is not None
        assert refreshed.last_login_at != before

    def test_logout_clears_session(self, client, admin_user):
        _login(client, "admin@example.com", "correct-horse-battery-staple")
        assert client.get("/admin/protected").status_code == 200
        logout = client.get("/admin/logout", follow_redirects=False)
        assert logout.status_code in (302, 200)
        # After logout, protected route is anonymous again -> 401.
        assert client.get("/admin/protected").status_code == 401


# ---------------------------------------------------------------------------
# Shadow-mode safety
# ---------------------------------------------------------------------------
class TestShadowMode:
    """When ADMIN_AUTH_ENABLED is unset, decorator must be a passthrough.

    This protects existing /admin* routes during the rollout window between
    deploying the auth code and flipping the flag in Coolify.
    """

    def test_decorator_passthrough_when_flag_unset(self, engine, monkeypatch):
        monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)

        flask_app = Flask(__name__, template_folder="../../review_app/templates")
        flask_app.config["SECRET_KEY"] = "test"
        flask_app.config["WTF_CSRF_ENABLED"] = False
        init_auth(flask_app)

        @flask_app.route("/admin/wide-open")
        @requires_role("admin")
        def wide_open():
            return "anyone-can-see-this"

        client = flask_app.test_client()
        resp = client.get("/admin/wide-open")
        assert resp.status_code == 200
        assert resp.data == b"anyone-can-see-this"

    def test_decorator_passthrough_when_flag_explicitly_false(self, engine, monkeypatch):
        monkeypatch.setenv("ADMIN_AUTH_ENABLED", "false")

        flask_app = Flask(__name__, template_folder="../../review_app/templates")
        flask_app.config["SECRET_KEY"] = "test"
        flask_app.config["WTF_CSRF_ENABLED"] = False
        init_auth(flask_app)

        @flask_app.route("/admin/wide-open-2")
        @requires_role("admin")
        def wide_open():
            return "still-open"

        client = flask_app.test_client()
        assert client.get("/admin/wide-open-2").status_code == 200


# ---------------------------------------------------------------------------
# Model-level smoke
# ---------------------------------------------------------------------------
class TestUserModel:
    def test_hash_and_verify_round_trip(self):
        h = User.hash_password("hunter2hunter2")
        u = User(email="x@y.com", password_hash=h, role="admin")
        assert u.verify_password("hunter2hunter2") is True
        assert u.verify_password("wrong") is False

    def test_invalid_role_rejected(self):
        with pytest.raises(ValueError):
            User.create(email="x@y.com", password="abcdefghijkl", role="wizard")

    def test_email_normalized_lowercase(self):
        u = User.create(email="MixedCase@Example.COM", password="abcdefghijkl", role="staff")
        assert u.email == "mixedcase@example.com"

    def test_get_active_by_email_case_insensitive(self, db_session):
        u = User.create(email="case@example.com", password="abcdefghijkl", role="admin")
        db_session.add(u)
        db_session.commit()
        found = User.get_active_by_email(db_session, "CASE@EXAMPLE.COM")
        assert found is not None
        assert found.id == u.id

    def test_soft_deleted_user_not_returned(self, db_session):
        from datetime import datetime, timezone
        u = User.create(email="ghost@example.com", password="abcdefghijkl", role="admin")
        u.deleted_at = datetime.now(timezone.utc)
        db_session.add(u)
        db_session.commit()
        assert User.get_active_by_email(db_session, "ghost@example.com") is None
        assert User.get_active_by_id(db_session, u.id) is None


# ---------------------------------------------------------------------------
# Postgres integration markers
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestPostgresIntegration:
    """Skipped by default; run with `pytest -m integration` against a live DB."""

    def test_citext_extension_created(self):
        if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
            pytest.skip("requires Postgres DATABASE_URL")
        # Smoke: just verify the migration ran and citext is available.
        from sqlalchemy import text
        eng = create_engine(os.environ["DATABASE_URL"])
        with eng.connect() as conn:
            row = conn.execute(text("SELECT extname FROM pg_extension WHERE extname='citext'")).fetchone()
            assert row is not None, "citext extension not installed; run alembic upgrade head"
