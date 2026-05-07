"""orders.internal_cost_cents — captures Prodigi cost on reprints.

Revision ID: 0021_reprint_cost
Revises: 0020_orders_production_ts
Create Date: 2026-05-05

Phase 5b: dedicated column for tracking what we paid Prodigi on reprint
orders (and any other internal-cost flows). The Phase 4b workaround stuffed
the reprint cost into ``orders.tax_cents`` to avoid a schema change; this
migration reclaims that field by moving the values into the new column on
reprint-source rows and zeroing the misused tax_cents.

We narrow the move to ``source = 'reprint'`` so we don't accidentally clobber
real tax values once Stripe Tax is wired up by Phase 5a.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_reprint_cost"
down_revision: str | Sequence[str] | None = "0020_orders_production_ts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(
            sa.Column(
                "internal_cost_cents",
                sa.BigInteger(),
                nullable=True,
            )
        )

    op.execute(
        """
        UPDATE orders
           SET internal_cost_cents = tax_cents,
               tax_cents = 0
         WHERE source = 'reprint'
           AND tax_cents > 0
           AND internal_cost_cents IS NULL
        """
    )


def downgrade() -> None:
    # Move values back into tax_cents to reverse the Phase 4b workaround.
    op.execute(
        """
        UPDATE orders
           SET tax_cents = COALESCE(internal_cost_cents, 0)
         WHERE source = 'reprint'
           AND internal_cost_cents IS NOT NULL
        """
    )
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("internal_cost_cents")
