"""Pytest configuration and shared fixtures for the wildprint test suite.

This module owns the test harness conventions:

* Unit tests are the default (fast, hermetic, no external services).
* Integration tests are marked with ``@pytest.mark.integration`` and are
  skipped unless the user passes ``--integration`` (or sets the equivalent
  service URLs and runs them by name).
* The Flask ``app`` / ``client`` fixtures wrap the legacy monolithic
  ``review_app.app`` module via a lightweight ``create_app()`` shim so that
  tests can exercise routes without rewriting the existing app file.
* The ``db_session`` fixture gives every test a SAVEPOINT-rolled-back
  in-memory SQLite session; if ``DATABASE_URL`` is set in the environment,
  it routes through that database instead (useful for local Postgres runs).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Iterator

import pytest

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# CLI flag — opt INTO integration tests
# ---------------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--integration`` flag.

    By default, ``@pytest.mark.integration`` tests are skipped so that
    ``pytest`` runs cleanly on a developer laptop with no Postgres / Redis /
    network access. Pass ``--integration`` to run them.
    """
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run @pytest.mark.integration tests (requires real DB/Redis/network).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip integration-marked tests unless ``--integration`` was passed."""
    if config.getoption("--integration"):
        return
    skip_integration = pytest.mark.skip(
        reason="needs --integration flag (real DB/Redis/network)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# Flask app + client
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def app() -> "Flask":
    """Return a Flask app instance configured for testing.

    Prefers ``review_app.create_app(testing=True)`` if defined; otherwise
    falls back to importing the legacy ``review_app.app:app`` module-level
    instance and flipping ``TESTING`` on. This keeps the existing monolith
    untouched while still giving tests a real WSGI app to talk to.
    """
    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-do-not-use-in-prod")

    try:
        from review_app import create_app  # type: ignore[attr-defined]
    except ImportError:
        create_app = None  # type: ignore[assignment]

    if create_app is not None:
        flask_app = create_app(testing=True)
    else:
        # Legacy fallback — import the module-level app.
        from review_app.app import app as flask_app  # type: ignore[no-redef]

    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="test-secret-key-do-not-use-in-prod",
    )
    return flask_app


@pytest.fixture()
def client(app: "Flask") -> "FlaskClient":
    """Flask test client — function scope so cookies don't bleed between tests."""
    return app.test_client()


@pytest.fixture()
def app_context(app: "Flask") -> Iterator[None]:
    """Push an application context for tests that touch ``current_app``."""
    with app.app_context():
        yield


# ---------------------------------------------------------------------------
# Database — sqlite in-memory by default; DATABASE_URL respected
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def db_engine() -> Iterator["Engine"]:
    """Build (and tear down) a SQLAlchemy engine for the test session.

    Uses an in-memory SQLite database by default. If ``DATABASE_URL`` is
    set in the environment (e.g. when running against a local Postgres),
    that URL is honored instead.
    """
    from sqlalchemy import create_engine

    url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # Allow the engine's connection to be shared across the session.
        connect_args["check_same_thread"] = False

    engine = create_engine(url, future=True, connect_args=connect_args)

    # Create all tables registered against ``Base`` so model tests can run
    # without booting Alembic.
    try:
        from review_app.db import Base
        Base.metadata.create_all(engine)
    except ImportError:
        # The db scaffold is allowed to be absent in very early tests.
        pass

    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: "Engine") -> Iterator["Session"]:
    """Per-test SQLAlchemy session that rolls back on teardown.

    Pattern: open a connection, begin an outer transaction, bind a Session
    to that connection, and roll the whole thing back when the test ends.
    Nothing the test commits actually persists.
    """
    from sqlalchemy.orm import Session as SASession

    connection = db_engine.connect()
    transaction = connection.begin()
    session = SASession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Redis — fakeredis for queue tests
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_redis() -> Iterator[Any]:
    """Fresh in-memory fake Redis for one test.

    Tests that need RQ should also call ``review_app.queue.reset_for_tests()``
    after wiring this connection in (the queue module caches its connection).
    """
    import fakeredis

    conn = fakeredis.FakeRedis()
    try:
        yield conn
    finally:
        conn.flushall()
        try:
            from review_app import queue as queue_module
            queue_module.reset_for_tests()
        except (ImportError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# Auth — Flask-Login style mock user (for tests added in Round 2 by tests/auth/)
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_user() -> Any:
    """A minimal user object compatible with Flask-Login's interface.

    Round 2 auth tests can monkeypatch ``flask_login.current_user`` or the
    project's session-loader to return this. Kept dependency-free so the
    fixture works even before flask-login is installed.
    """

    class MockUser:
        is_authenticated = True
        is_active = True
        is_anonymous = False

        def __init__(
            self,
            user_id: str = "test-user-uuid7-0000",
            email: str = "test@example.com",
            is_admin: bool = False,
        ) -> None:
            self.id = user_id
            self.email = email
            self.is_admin = is_admin

        def get_id(self) -> str:
            return self.id

    return MockUser()


@pytest.fixture()
def mock_admin_user(mock_user: Any) -> Any:
    """Same as ``mock_user`` but with ``is_admin=True``."""
    mock_user.is_admin = True
    return mock_user
