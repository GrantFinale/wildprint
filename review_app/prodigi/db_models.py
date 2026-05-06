"""SQLAlchemy ORM models for the four Prodigi tables.

Mirrors the schemas in:

* ``alembic/versions/0005_prodigi_orders.py`` — creates the tables
* ``alembic/versions/0006_seed_prodigi_skus.py`` — seeds the launch catalog

Note: the ``orders`` table doesn't exist yet (it lands in Phase 3). The
``fishingposter_order_id`` columns on ``ProdigiOrder`` and ``Shipment``
are nullable for now and will gain their FK constraints in a Phase 3
migration. ``ProdigiCallback.prodigi_order_id`` is a free-form TEXT (not
an FK to ``prodigi_orders``) because callbacks can arrive before we've
inserted our local row, and we want to capture them anyway.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, TimestampMixin, UUIDPKMixin


# Cross-dialect JSON / UUID column types — JSONB on Postgres, JSON on SQLite.
def _json_col() -> Any:
    """Return a JSON-compatible column type that works on both pg and sqlite."""
    return JSON().with_variant(JSONB(), "postgresql")


def _uuid_col() -> Any:
    """Return a UUID-compatible column type that works on both pg and sqlite."""
    return UUID(as_uuid=True).with_variant(Text(), "sqlite")


# ---------------------------------------------------------------------------
# prodigi_orders
# ---------------------------------------------------------------------------
class ProdigiOrder(Base, UUIDPKMixin, TimestampMixin):
    """One-to-one with our ``orders`` table (Phase 3) keyed by idempotency_key.

    The ``prodigi_order_id`` is null until POST /Orders succeeds. ``raw_snapshot``
    is the latest full GET /Orders/{id} response — JSONB so we can introspect
    historical fields without migrations.
    """

    __tablename__ = "prodigi_orders"

    fishingposter_order_id: Mapped[uuid.UUID | None] = mapped_column(
        _uuid_col(), nullable=True, index=True
    )
    prodigi_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_details: Mapped[dict[str, Any] | None] = mapped_column(
        _json_col(), nullable=True
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        _json_col(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_prodigi_orders_idempotency_key"),
        Index(
            "ix_prodigi_orders_prodigi_order_id",
            "prodigi_order_id",
            unique=True,
            postgresql_where=text("prodigi_order_id IS NOT NULL"),
            sqlite_where=text("prodigi_order_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# prodigi_skus
# ---------------------------------------------------------------------------
class ProdigiSku(Base):
    """The product catalog row for one (size x finish) variant.

    ``internal_sku`` is the primary key — short, stable, human-readable. It's
    referenced by Phase 3 cart/order_item rows. ``last_quoted_wholesale_cents``
    is filled in by :func:`review_app.prodigi.quote_refresh.refresh_all_skus_job`.
    """

    __tablename__ = "prodigi_skus"

    internal_sku: Mapped[str] = mapped_column(Text, primary_key=True)
    prodigi_sku: Mapped[str] = mapped_column(Text, nullable=False)
    finish: Mapped[str] = mapped_column(Text, nullable=False)
    size_inches: Mapped[str] = mapped_column(Text, nullable=False)
    orientation: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    retail_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_quoted_wholesale_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    margin_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "orientation IN ('portrait', 'landscape', 'square')",
            name="ck_prodigi_skus_orientation",
        ),
        Index("ix_prodigi_skus_prodigi_sku", "prodigi_sku"),
        Index("ix_prodigi_skus_active", "active"),
    )


# ---------------------------------------------------------------------------
# prodigi_callbacks
# ---------------------------------------------------------------------------
class ProdigiCallback(Base):
    """One row per inbound webhook payload.

    ``event_id`` is UNIQUE — a duplicate POST is a no-op. ``raw_payload``
    captures the full CloudEvents envelope so we can reprocess if the
    handler logic changes.
    """

    __tablename__ = "prodigi_callbacks"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    prodigi_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(_json_col(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_prodigi_callbacks_event_id"),
        CheckConstraint(
            "processed_status IN ('pending','ok','error','retry','ignored')",
            name="ck_prodigi_callbacks_processed_status",
        ),
        Index(
            "ix_prodigi_callbacks_prodigi_order_id",
            "prodigi_order_id",
            postgresql_where=text("prodigi_order_id IS NOT NULL"),
            sqlite_where=text("prodigi_order_id IS NOT NULL"),
        ),
        Index(
            "ix_prodigi_callbacks_unprocessed",
            "received_at",
            postgresql_where=text("processed_status IN ('pending','retry')"),
            sqlite_where=text("processed_status IN ('pending','retry')"),
        ),
    )


# ---------------------------------------------------------------------------
# shipments
# ---------------------------------------------------------------------------
class Shipment(Base, UUIDPKMixin, TimestampMixin):
    """One row per Prodigi shipment (an order can split into multiples)."""

    __tablename__ = "shipments"

    fishingposter_order_id: Mapped[uuid.UUID | None] = mapped_column(
        _uuid_col(), nullable=True, index=True
    )
    prodigi_shipment_id: Mapped[str] = mapped_column(Text, nullable=False)
    prodigi_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier_service: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "prodigi_shipment_id", name="uq_shipments_prodigi_shipment_id"
        ),
        Index("ix_shipments_prodigi_order_id", "prodigi_order_id"),
    )


# Convenience: reference to a foreign-key constraint we'll add in Phase 3.
# Importing this name so future migrations can reuse the constraint name.
FUTURE_FK_FISHINGPOSTER_ORDER = ForeignKey


__all__ = [
    "ProdigiCallback",
    "ProdigiOrder",
    "ProdigiSku",
    "Shipment",
]
