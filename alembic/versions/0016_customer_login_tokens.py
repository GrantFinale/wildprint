"""customer_login_tokens — magic-link login tokens for /account/* customer auth.

Revision ID: 0016_customer_login_tokens
Revises: 0015_audit_log
Create Date: 2026-05-05

Phase 5b: customer-facing storefront authenticates with magic links instead
of passwords. Each ``POST /account/login`` issues a one-shot token recorded
here; clicking the email link verifies + sets ``session['customer_id']``.

Chains after Phase 5a's ``0015_audit_log`` since the parallel agent has
already shipped that migration on the shared branch.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_customer_login_tokens"
down_revision: str | Sequence[str] | None = "0015_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "customer_login_tokens",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("customer_id", _uuid_type(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
            name="fk_customer_login_tokens_customer_id_customers",
        ),
        sa.UniqueConstraint(
            "token_hash", name="uq_customer_login_tokens_token_hash"
        ),
    )
    op.create_index(
        "ix_customer_login_tokens_customer_id",
        "customer_login_tokens",
        ["customer_id"],
    )
    op.create_index(
        "ix_customer_login_tokens_expires_at",
        "customer_login_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_login_tokens_expires_at",
        table_name="customer_login_tokens",
    )
    op.drop_index(
        "ix_customer_login_tokens_customer_id",
        table_name="customer_login_tokens",
    )
    op.drop_table("customer_login_tokens")
