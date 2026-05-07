"""audit_log — append-only admin action trail.

Revision ID: 0015_audit_log
Revises: 0014_stripe_events
Create Date: 2026-05-05

Phase 5a: middleware in :mod:`review_app.audit` records every state-changing
admin request (POST/PATCH/DELETE under ``/admin/*``) plus any explicit
:func:`review_app.audit.record` call inside business code.

Schema (mirrors ``docs/db-schema.md`` §16):

* ``id``        BIGSERIAL PK (rowid alias on SQLite).
* ``user_id``   UUID NULL — FK to ``users(id)`` ON DELETE SET NULL so
                deactivating a user doesn't break their historical trail.
* ``action``    TEXT NOT NULL — dotted name (``order.refund``, ``sku.update``,
                ``http.POST`` for the auto-captured catch-all).
* ``target_type``  TEXT NULL — model name or resource type (``order``, ``sku``).
* ``target_id``    TEXT NULL — string-typed so it can hold UUIDs, ints, slugs.
* ``before``    JSONB NULL — entity snapshot before the change.
* ``after``     JSONB NULL — entity snapshot after the change.
* ``ip_address`` INET NULL — Postgres-native IP type; degrades to TEXT on
                SQLite via with_variant.
* ``user_agent`` TEXT NULL.
* ``created_at`` TIMESTAMPTZ NOT NULL DEFAULT now().

Append-only by convention. The middleware never UPDATEs, never DELETEs;
admin code is the only writer. We don't enforce immutability at the DB
level (no row-level security, no triggers) so dev-mode debugging can still
clear rows from the table directly.

Indexes:
  * (user_id, created_at DESC) — "what did this user do recently"
  * (action, created_at DESC)  — "every refund in the last 24h"
  * (target_type, target_id, created_at DESC) — "history for THIS order"
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0015_audit_log"
down_revision: str | Sequence[str] | None = "0014_stripe_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bigint_pk() -> Any:
    """BIGINT autoincrement PK on Postgres; INTEGER (rowid alias) on SQLite."""
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _uuid_col() -> Any:
    """Native UUID on Postgres; CHAR(36) on SQLite."""
    return UUID(as_uuid=True).with_variant(sa.String(36), "sqlite")


def _json_col() -> Any:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _ip_col() -> Any:
    """INET on Postgres; TEXT on SQLite (no native IP type)."""
    return sa.Text().with_variant(INET(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            _bigint_pk(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            _uuid_col(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("before", _json_col(), nullable=True),
        sa.Column("after", _json_col(), nullable=True),
        sa.Column("ip_address", _ip_col(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_audit_log_user_created",
        "audit_log",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_action_created",
        "audit_log",
        ["action", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_target_created",
        "audit_log",
        ["target_type", "target_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_target_created", table_name="audit_log")
    op.drop_index("ix_audit_log_action_created", table_name="audit_log")
    op.drop_index("ix_audit_log_user_created", table_name="audit_log")
    op.drop_table("audit_log")
