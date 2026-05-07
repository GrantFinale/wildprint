"""stripe_events — dedup table for inbound Stripe webhook events.

Revision ID: 0014_stripe_events
Revises: 0013_migrate_leads_json
Create Date: 2026-05-05

Phase 3b: every Stripe webhook delivery is dedup'd against this table by
``event_id``. The webhook handler's first action is INSERT … ON CONFLICT
DO NOTHING (or the SQLite IntegrityError equivalent) — if a duplicate is
detected we return 200 immediately without re-running the side effects.

``raw_payload`` (JSONB on Postgres, JSON on SQLite) stores the verified
event body so we can replay handlers when business logic changes.

Schema notes:
* BIGSERIAL PK — rows are append-only, high-volume, single-writer.
* ``event_type`` indexed for cheap "show me all checkout.session.completed
  in the last hour" admin queries.
* ``processed_status`` is nullable: NULL = inserted but handler hasn't
  finished yet (or crashed mid-way). 'ok' = success, 'error' = caught
  exception. The CHECK constraint allows NULL for the in-flight state.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0014_stripe_events"
down_revision: str | Sequence[str] | None = "0013_migrate_leads_json"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> Any:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _bigint_pk() -> Any:
    """BIGINT autoincrement PK on Postgres; INTEGER (rowid alias) on SQLite."""
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column(
            "id",
            _bigint_pk(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("raw_payload", _json_type(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_status", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_stripe_events_event_id"),
        sa.CheckConstraint(
            "processed_status IS NULL OR processed_status IN ('ok','error')",
            name="ck_stripe_events_processed_status",
        ),
    )
    op.create_index(
        "ix_stripe_events_event_type", "stripe_events", ["event_type"]
    )
    op.create_index(
        "ix_stripe_events_received_at", "stripe_events", ["received_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_stripe_events_received_at", table_name="stripe_events")
    op.drop_index("ix_stripe_events_event_type", table_name="stripe_events")
    op.drop_table("stripe_events")
