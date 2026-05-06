"""SQLAlchemy 2.0 engine + session module.

Exports `engine`, `SessionLocal`, `Base`, `get_session` for use by Flask
handlers, Alembic, and pytest fixtures.

The engine is created lazily so that `import review_app.db` never raises in
environments without a `DATABASE_URL` (CI lint, local script imports, etc.).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from review_app.db.base import Base


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker[Session]] = None
_scoped_session: Optional[scoped_session[Session]] = None


def _resolve_database_url() -> str:
    """Resolve the database URL.

    Priority:
      1. `DATABASE_URL` env var
      2. `sqlite:///dev.db` fallback for local smoke tests

    The fallback exists so that `import review_app.db` and a quick
    `alembic upgrade head` work on a fresh checkout without provisioning
    Postgres.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return "sqlite:///dev.db"


def _build_engine(url: str) -> Engine:
    # SQLite needs `check_same_thread=False` so the per-request scoped session
    # works under Flask's threaded dev server. Postgres ignores it.
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def get_engine() -> Engine:
    """Return the process-wide engine, building it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine(_resolve_database_url())
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the unbound sessionmaker, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_factory


def get_scoped_session() -> scoped_session[Session]:
    """Return the Flask-context-bound scoped session."""
    global _scoped_session
    if _scoped_session is None:
        # scopefunc=None defaults to thread-local; session.py overrides this
        # with Flask's `_app_ctx_stack.__ident_func__` when wired into Flask.
        _scoped_session = scoped_session(get_session_factory())
    return _scoped_session


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-manager session for one-off scripts and tests.

    Commits on clean exit, rolls back on exception, always closes.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Module-level convenience aliases. These resolve the engine/session lazily
# via __getattr__ so that `from review_app.db import engine, SessionLocal`
# doesn't require DATABASE_URL at import time.
def __getattr__(name: str) -> object:
    if name == "engine":
        return get_engine()
    if name == "SessionLocal":
        return get_session_factory()
    if name == "db_session":
        return get_scoped_session()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "db_session",
    "get_engine",
    "get_session",
    "get_session_factory",
    "get_scoped_session",
]
