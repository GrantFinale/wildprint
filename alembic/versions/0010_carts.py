"""carts + cart_items — multi-item shopping cart.

Revision ID: 0010_carts
Revises: 0009_addresses
Create Date: 2026-05-05

Phase 3a:

* Anonymous carts allowed: ``carts.customer_id`` is NULL until the visitor
  signs in / pays. ``session_token`` provides anonymous identity.
* On customer delete we SET NULL on the cart rather than CASCADE so the cart
  becomes orphaned/abandoned rather than vanishing — easier to debug refunds.
* ``cart_items.unit_price_cents`` is a snapshot so price changes mid-cart
  don't surprise the customer.
* ``render_spec_id`` is nullable: a cart line is "this SKU + this poster".
  If render_specs ever gets pruned we set the FK to NULL rather than losing
  the cart line entirely.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_carts"
down_revision: str | Sequence[str] | None = "0009_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "customer_id",
            _uuid_type(),
            sa.ForeignKey(
                "customers.id",
                ondelete="SET NULL",
                name="fk_carts_customer_id_customers",
            ),
            nullable=True,
        ),
        sa.Column("session_token", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
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
            "status IN ('open', 'abandoned', 'converted')",
            name="ck_carts_status",
        ),
    )
    op.create_index("ix_carts_customer_id", "carts", ["customer_id"])
    op.create_index("ix_carts_session_token", "carts", ["session_token"])

    op.create_table(
        "cart_items",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "cart_id",
            _uuid_type(),
            sa.ForeignKey(
                "carts.id",
                ondelete="CASCADE",
                name="fk_cart_items_cart_id_carts",
            ),
            nullable=False,
        ),
        sa.Column(
            "render_spec_id",
            _uuid_type(),
            sa.ForeignKey(
                "render_specs.id",
                ondelete="SET NULL",
                name="fk_cart_items_render_spec_id_render_specs",
            ),
            nullable=True,
        ),
        sa.Column(
            "prodigi_sku_internal",
            sa.Text(),
            sa.ForeignKey(
                "prodigi_skus.internal_sku",
                ondelete="RESTRICT",
                name="fk_cart_items_prodigi_sku_internal_prodigi_skus",
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
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
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_cart_items_unit_price_non_negative",
        ),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])


def downgrade() -> None:
    op.drop_index("ix_cart_items_cart_id", table_name="cart_items")
    op.drop_table("cart_items")
    op.drop_index("ix_carts_session_token", table_name="carts")
    op.drop_index("ix_carts_customer_id", table_name="carts")
    op.drop_table("carts")
