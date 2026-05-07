"""orders + order_items + FK from prodigi_orders.order_id → orders.id.

Revision ID: 0011_orders
Revises: 0010_carts
Create Date: 2026-05-05

Phase 3a:

* ``orders`` is the canonical record of one customer transaction
  (potentially multiple line items). Money in BIGINT cents per
  ``docs/db-schema.md``.
* ``ON DELETE RESTRICT`` for customer + shipping_address — never let
  a cascading delete blow away a paid order's record. We soft-delete
  customers; addresses are CASCADE on customer for unpaid prep, but
  once an order references an address, the order's RESTRICT FK
  prevents that address from being CASCADE-deleted via the customer
  (Postgres evaluates RESTRICT before CASCADE on the same parent).
  In practice we never hard-delete a customer that has paid orders.
* ``stripe_payment_intent_id`` is UNIQUE — Stripe's idempotency anchor.
* ``order_items`` snapshots ``finish_display`` and ``size_inches`` so
  fulfillment history is preserved even if SKUs get retired.
* This migration also adds ``prodigi_orders.order_id`` (nullable) +
  the FK back to ``orders``. Existing prodigi_orders rows get NULL.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_orders"
down_revision: str | Sequence[str] | None = "0010_carts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "customer_id",
            _uuid_type(),
            sa.ForeignKey(
                "customers.id",
                ondelete="RESTRICT",
                name="fk_orders_customer_id_customers",
            ),
            nullable=False,
        ),
        sa.Column(
            "shipping_address_id",
            _uuid_type(),
            sa.ForeignKey(
                "addresses.id",
                ondelete="RESTRICT",
                name="fk_orders_shipping_address_id_addresses",
            ),
            nullable=True,
        ),
        sa.Column("stripe_payment_intent_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("subtotal_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "shipping_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tax_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'web'"),
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
            "status IN ('pending','paid','in_production','shipped',"
            "'delivered','refunded','cancelled','problem')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint(
            "subtotal_cents >= 0", name="ck_orders_subtotal_non_negative"
        ),
        sa.CheckConstraint(
            "shipping_cents >= 0", name="ck_orders_shipping_non_negative"
        ),
        sa.CheckConstraint("tax_cents >= 0", name="ck_orders_tax_non_negative"),
        sa.CheckConstraint(
            "total_cents >= 0", name="ck_orders_total_non_negative"
        ),
        sa.UniqueConstraint(
            "stripe_payment_intent_id",
            name="uq_orders_stripe_payment_intent_id",
        ),
    )
    op.create_index(
        "ix_orders_customer_id_created_at",
        "orders",
        ["customer_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_orders_status_created_at",
        "orders",
        ["status", sa.text("created_at DESC")],
    )

    op.create_table(
        "order_items",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "order_id",
            _uuid_type(),
            sa.ForeignKey(
                "orders.id",
                ondelete="RESTRICT",
                name="fk_order_items_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column(
            "render_spec_id",
            _uuid_type(),
            sa.ForeignKey(
                "render_specs.id",
                ondelete="RESTRICT",
                name="fk_order_items_render_spec_id_render_specs",
            ),
            nullable=True,
        ),
        sa.Column(
            "prodigi_sku_internal",
            sa.Text(),
            sa.ForeignKey(
                "prodigi_skus.internal_sku",
                ondelete="RESTRICT",
                name="fk_order_items_prodigi_sku_internal_prodigi_skus",
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("line_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("finish_display", sa.Text(), nullable=False),
        sa.Column("size_inches", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_order_items_quantity_positive"
        ),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_order_items_unit_price_non_negative",
        ),
        sa.CheckConstraint(
            "line_total_cents >= 0",
            name="ck_order_items_line_total_non_negative",
        ),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    # ------------------------------------------------------------------
    # Add prodigi_orders.order_id (nullable) + FK to orders.
    # Existing rows get NULL. New rows set this when fulfillment kicks off.
    # SQLite needs batch mode for ALTER TABLE — env.py already enables it.
    # ------------------------------------------------------------------
    with op.batch_alter_table("prodigi_orders") as batch:
        batch.add_column(
            sa.Column("order_id", _uuid_type(), nullable=True),
        )
        batch.create_foreign_key(
            "fk_prodigi_orders_order_id_orders",
            "orders",
            ["order_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_prodigi_orders_order_id",
            ["order_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("prodigi_orders") as batch:
        batch.drop_index("ix_prodigi_orders_order_id")
        batch.drop_constraint(
            "fk_prodigi_orders_order_id_orders", type_="foreignkey"
        )
        batch.drop_column("order_id")

    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_index("ix_orders_customer_id_created_at", table_name="orders")
    op.drop_table("orders")
