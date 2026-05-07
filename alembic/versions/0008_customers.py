"""customers — buyer accounts (post-purchase or anonymous lead-tracked).

Revision ID: 0008_customers
Revises: 0007_render_specs
Create Date: 2026-05-05

Phase 3a: introduces the canonical ``customers`` table that anchors the new
order/address/refund schema.

Schema decisions (see ``docs/db-schema.md`` and ``docs/phase-3-schema.md``):

* PK is UUID (``sa.Uuid(as_uuid=True)``). On Postgres this is the native UUID
  type; on SQLite it degrades to a 32-char hex string. SQLAlchemy round-trips
  :class:`uuid.UUID` Python objects on both. This is the same pattern used by
  ``0007_render_specs`` and is the project's go-forward convention.
* Email uses CITEXT on Postgres for case-insensitive uniqueness without
  ``lower()`` everywhere — same pattern as ``0002_users``. SQLite falls back
  to plain TEXT; the application normalizes via ``.strip().lower()``.
* Soft delete via ``deleted_at`` plus a partial unique index on email so
  reactivating a soft-deleted email isn't blocked.
* ``legacy_lake_name`` and ``legacy_state`` preserve the lake/state context
  captured by the original ``metadata/leads.json`` flow that doesn't fit the
  orders model. They're nullable for new (non-legacy) customers.
* ``created_by_migration`` is a free-form provenance tag — the leads.json
  data migration sets it to ``'0013_migrate_leads_json'`` so its
  ``downgrade()`` knows precisely which rows it owns.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_customers"
down_revision: str | Sequence[str] | None = "0007_render_specs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _uuid_type() -> Any:
    """Cross-dialect UUID column — native on Postgres, hex string on SQLite."""
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS citext")
        email_type: sa.types.TypeEngine[str] = sa.dialects.postgresql.CITEXT()
    else:
        email_type = sa.Text()

    op.create_table(
        "customers",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("email", email_type, nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column(
            "marketing_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Legacy fields preserved from metadata/leads.json (nullable for new
        # signups). See 0013_migrate_leads_json.py.
        sa.Column("legacy_lake_name", sa.Text(), nullable=True),
        sa.Column("legacy_state", sa.Text(), nullable=True),
        # Provenance — set by data migrations so downgrade() can target rows.
        sa.Column("created_by_migration", sa.Text(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Partial unique index on email (only enforce among non-deleted rows).
    op.create_index(
        "customers_email_active_uq",
        "customers",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_customers_stripe_customer_id",
        "customers",
        ["stripe_customer_id"],
        unique=True,
        postgresql_where=sa.text("stripe_customer_id IS NOT NULL"),
        sqlite_where=sa.text("stripe_customer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_customers_stripe_customer_id", table_name="customers")
    op.drop_index("customers_email_active_uq", table_name="customers")
    op.drop_table("customers")
    # Note: we don't DROP EXTENSION citext here because 0002_users may still
    # need it (and 0002's downgrade handles it).
