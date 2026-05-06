"""outbox table — transactional outbox for async side effects (email, etc.).

Revision ID: 0004_outbox
Revises: 0003_ai_usage
Create Date: 2026-05-05

Creates the `outbox` table per docs/db-schema.md §17 (with the Phase 0.5
extensions: `attempts`/`max_attempts`/`sent_at` columns and a `sending`
status). The table is the durable handoff between request handlers and the
queue worker — every email send (and eventually Prodigi order create, etc.)
inserts a row in the same DB transaction as the business mutation, then a
worker drains it asynchronously.

Schema notes:
- BIGSERIAL PK because rows are append-only and high-volume; we don't need
  UUID uniqueness across nodes here (single Postgres writer).
- `kind` is a free-form TEXT column (e.g. `email.order_confirmed`,
  `prodigi.create_order`); the application owns the namespace.
- `status` is a TEXT column with a CHECK constraint instead of a Postgres
  ENUM to keep migrations cheap and SQLite-portable for unit tests.
  `sending` is the in-flight state used by `claim_batch()` (FOR UPDATE
  SKIP LOCKED) so a crashed worker leaves a row visibly mid-flight rather
  than silently lost.
- `dead` is the terminal failure state — distinct from `failed`, which is
  retryable. After `attempts >= max_attempts`, `mark_failed` flips the row
  to `dead` and the worker stops touching it.
- `next_retry_at` defaults to `now()` so a freshly enqueued row is
  immediately eligible. Failed rows get bumped forward by exponential
  backoff (1m, 5m, 25m, 2h, 10h).
- `payload` is JSONB on Postgres / JSON on SQLite (BC for tests).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_outbox"
down_revision: Union[str, Sequence[str], None] = "0003_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    is_pg = _is_postgres()

    # JSONB on Postgres, JSON on SQLite — both expose dict-like access via
    # SQLAlchemy's JSON type adapter.
    if is_pg:
        payload_type: sa.types.TypeEngine = sa.dialects.postgresql.JSONB()
    else:
        payload_type = sa.JSON()

    # BigInteger PRIMARY KEY autoincrements on Postgres (BIGSERIAL); on
    # SQLite, only `INTEGER PRIMARY KEY` is a rowid alias, so we degrade
    # to plain INTEGER on the test path. The model in
    # `review_app.email.outbox` mirrors this dialect variant.
    if is_pg:
        id_type: sa.types.TypeEngine = sa.BigInteger()
    else:
        id_type = sa.Integer()

    op.create_table(
        "outbox",
        sa.Column(
            "id",
            id_type,
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", payload_type, nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'dead')",
            name="outbox_status_in_enum",
        ),
    )

    # (status, next_retry_at) — drives the worker's poll query:
    #   SELECT ... WHERE status IN ('pending','failed') AND next_retry_at <= now()
    #   ORDER BY next_retry_at FOR UPDATE SKIP LOCKED
    op.create_index(
        "outbox_status_next_retry_ix",
        "outbox",
        ["status", "next_retry_at"],
    )

    # (kind, created_at DESC) — admin lookups: "show me the last 50
    # email.order_confirmed rows" without scanning the whole table.
    op.create_index(
        "outbox_kind_created_ix",
        "outbox",
        ["kind", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("outbox_kind_created_ix", table_name="outbox")
    op.drop_index("outbox_status_next_retry_ix", table_name="outbox")
    op.drop_table("outbox")
