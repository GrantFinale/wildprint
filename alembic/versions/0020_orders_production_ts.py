"""orders.in_production_at — captures the moment Prodigi flips to InProgress.

Revision ID: 0020_orders_production_ts
Revises: 0019_notes
Create Date: 2026-05-05

Phase 5b: adds a real timestamp column for the ``in_production`` status
transition so the operations analytics page can compute
``AVG(in_production_at - paid_at)`` instead of the Phase 4b
``0.4 * time_to_ship`` placeholder.

Backfill: existing orders already in/past ``in_production`` get
``in_production_at = paid_at`` as a best-effort (we don't have the real
timestamp). New transitions write a real value via
``review_app.orders.jobs._enqueue_in_production_callback``.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_orders_production_ts"
down_revision: str | Sequence[str] | None = "0019_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(
            sa.Column(
                "in_production_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    # Best-effort backfill for existing rows.
    op.execute(
        """
        UPDATE orders
           SET in_production_at = paid_at
         WHERE in_production_at IS NULL
           AND status IN ('in_production', 'shipped', 'delivered')
           AND paid_at IS NOT NULL
        """
    )

    op.create_index(
        "ix_orders_in_production_at",
        "orders",
        ["in_production_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_in_production_at", table_name="orders")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("in_production_at")
