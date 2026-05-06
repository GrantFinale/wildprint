"""ai_usage_log table — per-call AI provider telemetry.

Revision ID: 0003_ai_usage
Revises: 0002_users
Create Date: 2026-05-05

Creates the `ai_usage_log` table per docs/db-schema.md §15. Every call to
OpenAI / Recraft / Replicate routed through `review_app.ai.*` wrappers
inserts one row here when `AI_LOGGING_ENABLED=1`.

Schema notes:
- BIGSERIAL PK because rows are append-only and high-volume; insert speed
  matters more than UUID uniqueness across nodes.
- `user_id` and `render_spec_id` are nullable foreign keys with ON DELETE
  SET NULL — telemetry rows survive user/spec deletion (we still want the
  cost record), but the FK becomes orphaned.
- `render_spec_id` FK is declared only when the `render_specs` table
  exists; on a fresh DB without it (Phase 0), we skip the FK constraint
  but keep the column so future migrations can backfill the constraint.
- `users` table is created by 0002_users — that migration is a hard
  prerequisite of this one (down_revision='0002_users').
- Status is a TEXT column with a CHECK constraint instead of a Postgres
  ENUM to keep migrations cheap and SQLite-portable for unit tests.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003_ai_usage"
down_revision: Union[str, Sequence[str], None] = "0002_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    is_pg = _is_postgres()

    # Type choices that adapt to dialect:
    # - id: BIGSERIAL on Postgres, INTEGER autoincrement on SQLite.
    # - uuid columns: native UUID on Postgres, TEXT(36) on SQLite.
    if is_pg:
        uuid_type: sa.types.TypeEngine = sa.dialects.postgresql.UUID(as_uuid=True)
        id_type: sa.types.TypeEngine = sa.BigInteger()
    else:
        uuid_type = sa.String(length=36)
        id_type = sa.BigInteger()

    columns = [
        sa.Column("id", id_type, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("render_spec_id", uuid_type, nullable=True),
        sa.Column("user_id", uuid_type, nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("units", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column(
            "cost_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error')",
            name="ai_usage_status_in_enum",
        ),
    ]

    # Conditionally add foreign keys only when the referenced tables exist.
    # Phase 0 may not yet have `render_specs`; the column exists for forward
    # compatibility but the FK is added by a later migration.
    if _table_exists("users"):
        columns.append(
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_ai_usage_log_user_id_users",
                ondelete="SET NULL",
            )
        )
    if _table_exists("render_specs"):
        columns.append(
            sa.ForeignKeyConstraint(
                ["render_spec_id"],
                ["render_specs.id"],
                name="fk_ai_usage_log_render_spec_id_render_specs",
                ondelete="SET NULL",
            )
        )

    op.create_table("ai_usage_log", *columns)

    # Indexes:
    # - (provider, created_at DESC) powers the admin dashboard's per-provider
    #   recent-events view.
    # - (created_at DESC) powers the global recent-events feed.
    op.create_index(
        "ai_usage_provider_created_ix",
        "ai_usage_log",
        ["provider", sa.text("created_at DESC")],
    )
    op.create_index(
        "ai_usage_created_ix",
        "ai_usage_log",
        [sa.text("created_at DESC")],
    )
    # Partial index on render_spec_id for FK-style lookups when populated.
    op.create_index(
        "ai_usage_render_spec_ix",
        "ai_usage_log",
        ["render_spec_id"],
        postgresql_where=sa.text("render_spec_id IS NOT NULL"),
        sqlite_where=sa.text("render_spec_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ai_usage_render_spec_ix", table_name="ai_usage_log")
    op.drop_index("ai_usage_created_ix", table_name="ai_usage_log")
    op.drop_index("ai_usage_provider_created_ix", table_name="ai_usage_log")
    op.drop_table("ai_usage_log")
