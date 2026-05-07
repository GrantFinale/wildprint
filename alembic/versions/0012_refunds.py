"""refunds — Stripe refund records + Prodigi cancel attempts.

Revision ID: 0012_refunds
Revises: 0011_orders
Create Date: 2026-05-05

Phase 3a:

* ``refunds.order_id`` is RESTRICT — never let a refund vanish if its order
  is removed (we never hard-delete orders anyway).
* ``stripe_refund_id`` is UNIQUE — Stripe's idempotency anchor.
* ``requested_by_user_id`` references the admin ``users`` table; SET NULL
  on user deletion preserves the audit trail (we lose "who" but keep "what").
* ``prodigi_cancel_attempted`` / ``prodigi_cancel_succeeded`` reflect the
  reality that Prodigi can refuse to cancel orders already in production —
  we record the attempt and outcome separately from the Stripe refund.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_refunds"
down_revision: str | Sequence[str] | None = "0011_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "order_id",
            _uuid_type(),
            sa.ForeignKey(
                "orders.id",
                ondelete="RESTRICT",
                name="fk_refunds_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column("stripe_refund_id", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "requested_by_user_id",
            _uuid_type(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_refunds_requested_by_user_id_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "prodigi_cancel_attempted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("prodigi_cancel_succeeded", sa.Boolean(), nullable=True),
        sa.Column(
            "customer_notified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed','cancelled')",
            name="ck_refunds_status",
        ),
        sa.CheckConstraint(
            "amount_cents > 0", name="ck_refunds_amount_positive"
        ),
        sa.UniqueConstraint(
            "stripe_refund_id", name="uq_refunds_stripe_refund_id"
        ),
    )
    op.create_index("ix_refunds_order_id", "refunds", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_refunds_order_id", table_name="refunds")
    op.drop_table("refunds")
