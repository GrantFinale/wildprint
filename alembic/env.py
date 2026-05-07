"""Alembic environment.

Reads `DATABASE_URL` from the environment, imports the project `Base` so that
autogenerate sees every model registered against `Base.metadata`, and runs
migrations in either offline (--sql) or online (live connection) mode.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project root importable so `from review_app.db ...` works regardless
# of where alembic is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_app.db.base import Base  # noqa: E402

# Importing model modules triggers their registration on Base.metadata so
# that Alembic autogenerate sees them. Add new sub-task model modules here.
from review_app.auth import models as _auth_models  # noqa: F401, E402
from review_app.ai import models as _ai_models  # noqa: F401, E402
from review_app.email import outbox as _email_outbox  # noqa: F401, E402
from review_app.prodigi import db_models as _prodigi_models  # noqa: F401, E402
from review_app.render import db_models as _render_models  # noqa: F401, E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        # Match the lazy fallback in review_app/db/__init__.py so that
        # `alembic upgrade head` works on a fresh checkout.
        url = "sqlite:///dev.db"
    return url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running against a live DB."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=_database_url().startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    cfg_section: dict[str, Any] = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=is_sqlite,  # SQLite needs batch mode for ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
