"""addresses — shipping addresses + Smarty validation cache.

Revision ID: 0009_addresses
Revises: 0008_customers
Create Date: 2026-05-05

Phase 3a:

* CASCADE delete on customer — an address has no meaning without its owner.
* ``validation_response`` is JSONB on Postgres / JSON on SQLite. We store the
  full Smarty response so a delivery dispute later doesn't require re-billing
  the lookup.
* ``dpv_match_code`` is the canonical USPS deliverability code we copy out of
  the Smarty response into its own column for fast filtering ('Y'=valid,
  'S'=valid+secondary missing, 'D'=valid+secondary missing-but-not-required,
  'N'=invalid). Anything else means we couldn't verify.
* Partial unique index on (customer_id) WHERE is_default — only one default
  shipping address per customer.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0009_addresses"
down_revision: str | Sequence[str] | None = "0008_customers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def _json_type() -> Any:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "addresses",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "customer_id",
            _uuid_type(),
            sa.ForeignKey(
                "customers.id",
                ondelete="CASCADE",
                name="fk_addresses_customer_id_customers",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("line1", sa.Text(), nullable=False),
        sa.Column("line2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("zip", sa.Text(), nullable=False),
        sa.Column(
            "country",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'US'"),
        ),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_provider", sa.Text(), nullable=True),
        sa.Column("validation_response", _json_type(), nullable=True),
        sa.Column("dpv_match_code", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "length(state) = 2",
            name="ck_addresses_state_two_chars",
        ),
        sa.CheckConstraint(
            "length(country) = 2",
            name="ck_addresses_country_two_chars",
        ),
    )
    op.create_index(
        "ix_addresses_customer_id",
        "addresses",
        ["customer_id"],
    )
    # One default shipping address per customer.
    op.create_index(
        "addresses_one_default_per_customer_uq",
        "addresses",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND deleted_at IS NULL"),
        sqlite_where=sa.text("is_default = 1 AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "addresses_one_default_per_customer_uq", table_name="addresses"
    )
    op.drop_index("ix_addresses_customer_id", table_name="addresses")
    op.drop_table("addresses")
