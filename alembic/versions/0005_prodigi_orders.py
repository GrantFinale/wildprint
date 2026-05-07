"""prodigi_orders, prodigi_skus, prodigi_callbacks, shipments tables.

Revision ID: 0005_prodigi_orders
Revises: 0003_ai_usage
Create Date: 2026-05-05

REBASE NOTE: this revision is currently chained directly off
``0003_ai_usage`` because the parallel Phase 0.5 work (which adds
``0004_outbox``) hasn't merged into this branch yet. When the
``phase-0-foundation`` branch (containing 0004_outbox) merges into
``phase-1-prodigi-client``:

  1. Rebase: change ``down_revision`` below from ``0003_ai_usage`` to
     ``0004_outbox``.
  2. The migration content stays identical; only the revision graph
     edge moves.

This is a pure linearization — no schema changes are required because
0004_outbox (the email outbox table) is independent of the four prodigi
tables we create here.

Creates the four Prodigi-integration tables per docs/db-schema.md
sections 10-13. The ``orders`` table doesn't exist yet (Phase 3), so:

* ``prodigi_orders.fishingposter_order_id`` — nullable, no FK yet
* ``shipments.fishingposter_order_id`` — nullable, no FK yet

A Phase 3 migration will:

1. Create the ``orders`` table.
2. ``ALTER`` these two columns to NOT NULL + add the FK constraints.

Schema notes:

* All UUIDs are native ``UUID`` on Postgres / ``TEXT(36)`` on SQLite —
  same dialect-aware pattern as 0003_ai_usage.py.
* JSONB on Postgres / JSON on SQLite for ``status_details``,
  ``raw_snapshot``, ``raw_payload``.
* ``prodigi_skus.margin_cents`` is a regular column rather than a
  generated column — Postgres generated columns require pg12+ which we
  have, but SQLite (used in unit tests) doesn't support them. The
  application will compute and write this value at the same time as
  ``last_quoted_wholesale_cents``. Trade-off documented in code comments
  near :func:`review_app.prodigi.quote_refresh.refresh_all_skus_job`.
* Partial indexes (``WHERE prodigi_order_id IS NOT NULL`` etc) are
  emitted as ``postgresql_where`` + ``sqlite_where`` so both backends
  honour the filter.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_prodigi_orders"
down_revision: Union[str, Sequence[str], None] = "0004_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    is_pg = _is_postgres()

    if is_pg:
        uuid_type: sa.types.TypeEngine = sa.dialects.postgresql.UUID(as_uuid=True)
        json_type: sa.types.TypeEngine = sa.dialects.postgresql.JSONB()
        big_id_type: sa.types.TypeEngine = sa.BigInteger()
    else:
        uuid_type = sa.String(length=36)
        json_type = sa.JSON()
        # SQLite autoincrement requires INTEGER, not BIGINT.
        big_id_type = sa.Integer()

    # ------------------------------------------------------------------
    # prodigi_orders
    # ------------------------------------------------------------------
    op.create_table(
        "prodigi_orders",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("fishingposter_order_id", uuid_type, nullable=True),
        sa.Column("prodigi_order_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status_stage", sa.Text(), nullable=True),
        sa.Column("status_details", json_type, nullable=True),
        sa.Column(
            "last_fetched_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("raw_snapshot", json_type, nullable=True),
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
        sa.UniqueConstraint(
            "idempotency_key", name="uq_prodigi_orders_idempotency_key"
        ),
    )
    op.create_index(
        "ix_prodigi_orders_fishingposter_order_id",
        "prodigi_orders",
        ["fishingposter_order_id"],
    )
    op.create_index(
        "ix_prodigi_orders_prodigi_order_id",
        "prodigi_orders",
        ["prodigi_order_id"],
        unique=True,
        postgresql_where=sa.text("prodigi_order_id IS NOT NULL"),
        sqlite_where=sa.text("prodigi_order_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # prodigi_skus
    # ------------------------------------------------------------------
    op.create_table(
        "prodigi_skus",
        sa.Column("internal_sku", sa.Text(), primary_key=True, nullable=False),
        sa.Column("prodigi_sku", sa.Text(), nullable=False),
        sa.Column("finish", sa.Text(), nullable=False),
        sa.Column("size_inches", sa.Text(), nullable=False),
        sa.Column("orientation", sa.Text(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("retail_price_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_quoted_wholesale_cents", sa.BigInteger(), nullable=True
        ),
        sa.Column("margin_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "in_stock", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "last_refreshed_at", sa.DateTime(timezone=True), nullable=True
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
            "orientation IN ('portrait', 'landscape', 'square')",
            name="ck_prodigi_skus_orientation",
        ),
    )
    op.create_index("ix_prodigi_skus_prodigi_sku", "prodigi_skus", ["prodigi_sku"])
    op.create_index("ix_prodigi_skus_active", "prodigi_skus", ["active"])

    # ------------------------------------------------------------------
    # prodigi_callbacks
    # ------------------------------------------------------------------
    op.create_table(
        "prodigi_callbacks",
        sa.Column(
            "id",
            big_id_type,
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("prodigi_order_id", sa.Text(), nullable=True),
        sa.Column("raw_payload", json_type, nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processed_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "processed_status IN ('pending','ok','error','retry','ignored')",
            name="ck_prodigi_callbacks_processed_status",
        ),
        sa.UniqueConstraint("event_id", name="uq_prodigi_callbacks_event_id"),
    )
    op.create_index(
        "ix_prodigi_callbacks_prodigi_order_id",
        "prodigi_callbacks",
        ["prodigi_order_id"],
        postgresql_where=sa.text("prodigi_order_id IS NOT NULL"),
        sqlite_where=sa.text("prodigi_order_id IS NOT NULL"),
    )
    op.create_index(
        "ix_prodigi_callbacks_unprocessed",
        "prodigi_callbacks",
        ["received_at"],
        postgresql_where=sa.text("processed_status IN ('pending','retry')"),
        sqlite_where=sa.text("processed_status IN ('pending','retry')"),
    )

    # ------------------------------------------------------------------
    # shipments
    # ------------------------------------------------------------------
    op.create_table(
        "shipments",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("fishingposter_order_id", uuid_type, nullable=True),
        sa.Column("prodigi_shipment_id", sa.Text(), nullable=False),
        sa.Column("prodigi_order_id", sa.Text(), nullable=True),
        sa.Column("carrier_name", sa.Text(), nullable=True),
        sa.Column("carrier_service", sa.Text(), nullable=True),
        sa.Column("tracking_number", sa.Text(), nullable=True),
        sa.Column("tracking_url", sa.Text(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "prodigi_shipment_id", name="uq_shipments_prodigi_shipment_id"
        ),
    )
    op.create_index(
        "ix_shipments_fishingposter_order_id",
        "shipments",
        ["fishingposter_order_id"],
    )
    op.create_index(
        "ix_shipments_prodigi_order_id",
        "shipments",
        ["prodigi_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_shipments_prodigi_order_id", table_name="shipments")
    op.drop_index("ix_shipments_fishingposter_order_id", table_name="shipments")
    op.drop_table("shipments")

    op.drop_index(
        "ix_prodigi_callbacks_unprocessed", table_name="prodigi_callbacks"
    )
    op.drop_index(
        "ix_prodigi_callbacks_prodigi_order_id", table_name="prodigi_callbacks"
    )
    op.drop_table("prodigi_callbacks")

    op.drop_index("ix_prodigi_skus_active", table_name="prodigi_skus")
    op.drop_index("ix_prodigi_skus_prodigi_sku", table_name="prodigi_skus")
    op.drop_table("prodigi_skus")

    op.drop_index(
        "ix_prodigi_orders_prodigi_order_id", table_name="prodigi_orders"
    )
    op.drop_index(
        "ix_prodigi_orders_fishingposter_order_id", table_name="prodigi_orders"
    )
    op.drop_table("prodigi_orders")
