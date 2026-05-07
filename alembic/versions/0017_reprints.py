"""reprint_requests — customer- or admin-initiated reprint requests.

Revision ID: 0017_reprints
Revises: 0016_customer_login_tokens
Create Date: 2026-05-05

Phase 5b: customers can request a free reprint within 30 days of delivery
(see ``review_app.refunds.reprints``); admins can also request a reprint at
any time. Approved reprints create a NEW Prodigi order at no charge to the
customer; the cost is captured in ``orders.internal_cost_cents`` on the new
reprint Order (see migration 0021).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_reprints"
down_revision: str | Sequence[str] | None = "0016_customer_login_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "reprint_requests",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("order_id", _uuid_type(), nullable=False),
        sa.Column("customer_id", _uuid_type(), nullable=False),
        sa.Column("requested_by_role", sa.Text(), nullable=False),
        sa.Column("line_item_ids", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "customer_paid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("new_prodigi_order_id", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", _uuid_type(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name="fk_reprint_requests_order_id_orders",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="RESTRICT",
            name="fk_reprint_requests_customer_id_customers",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_reprint_requests_decided_by_user_id_users",
        ),
        sa.CheckConstraint(
            "requested_by_role IN ('customer','admin')",
            name="ck_reprint_requests_requested_by_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','completed')",
            name="ck_reprint_requests_status",
        ),
    )
    op.create_index(
        "ix_reprint_requests_order_id", "reprint_requests", ["order_id"]
    )
    op.create_index(
        "ix_reprint_requests_customer_id",
        "reprint_requests",
        ["customer_id"],
    )
    op.create_index(
        "ix_reprint_requests_status_created_at",
        "reprint_requests",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reprint_requests_status_created_at",
        table_name="reprint_requests",
    )
    op.drop_index(
        "ix_reprint_requests_customer_id", table_name="reprint_requests"
    )
    op.drop_index(
        "ix_reprint_requests_order_id", table_name="reprint_requests"
    )
    op.drop_table("reprint_requests")
