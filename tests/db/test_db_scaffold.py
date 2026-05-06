"""Smoke tests for the SQLAlchemy + Alembic scaffold (Phase 0.2).

Unit tests run against in-memory SQLite. Integration tests against real
Postgres are gated on `DATABASE_URL` being set and marked
`@pytest.mark.integration`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_imports_clean() -> None:
    """The scaffold must import without DATABASE_URL set."""
    from review_app.db import Base, SessionLocal, engine  # noqa: F401
    assert Base is not None


def test_naming_convention_applied() -> None:
    """Constraint naming convention must match Alembic-friendly defaults."""
    from review_app.db import Base

    convention = Base.metadata.naming_convention
    assert convention["ix"] == "ix_%(column_0_label)s"
    assert convention["uq"] == "uq_%(table_name)s_%(column_0_name)s"
    assert convention["fk"] == "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    assert convention["pk"] == "pk_%(table_name)s"


def test_uuid7_generates_valid_v7() -> None:
    """uuid7() helper produces an RFC 9562 UUIDv7."""
    from review_app.db.base import uuid7

    u = uuid7()
    assert isinstance(u, uuid.UUID)
    assert u.version == 7
    # RFC 4122 variant: top two bits of clock_seq_hi must be 0b10
    assert (u.int >> 62) & 0b11 == 0b10


def test_uuid7_is_time_ordered() -> None:
    """uuid7() must be monotonic at millisecond resolution.

    Within a single millisecond the random bits dominate ordering (per RFC
    9562), so we sleep 2 ms between samples to assert the timestamp prefix
    is monotonically increasing.
    """
    import time

    from review_app.db.base import uuid7

    samples = []
    for _ in range(10):
        samples.append(uuid7())
        time.sleep(0.002)
    assert samples == sorted(samples)


def test_alembic_upgrade_head_against_sqlite(tmp_path: Path) -> None:
    """`alembic upgrade head` must run cleanly and create alembic_version."""
    db_file = tmp_path / "scaffold.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_file}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    # Verify alembic_version table exists and holds our baseline revision.
    import sqlite3

    conn = sqlite3.connect(db_file)
    try:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        conn.close()
    assert rows == [("0001_baseline",)]


def test_alembic_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Running `alembic upgrade head` twice must be a no-op the second time."""
    db_file = tmp_path / "idemp.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_file}"

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Integration — real Postgres. Skipped unless DATABASE_URL points at one.
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or "").startswith("postgresql"),
    reason="DATABASE_URL not set to a postgresql:// URL",
)
def test_engine_connects_to_postgres() -> None:
    from sqlalchemy import text

    from review_app.db import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
