"""Customer ORM model.

Mirrors ``alembic/versions/0008_customers.py``. See that migration for
schema rationale (CITEXT email, partial unique indexes, soft-delete, etc.).

The lead/customer lifecycle in Phase 3a:

1. A visitor enters their email on the landing page → write to ``leads.json``
   AND insert/upsert a ``customers`` row (no orders yet).
2. A visitor pays the $49 unlock → mark ``leads.json`` paid AND insert a
   synthetic ``orders`` row keyed by ``stripe_payment_intent_id``.
3. A visitor checks out a physical print → ``customers`` + ``addresses`` +
   ``orders`` + ``order_items``, fully relational.

Coexistence with the JSON file is intentional during the cutover; see
``docs/phase-3-schema.md``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_app.db.base import Base, TimestampMixin, uuid7

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from review_app.addresses.models import Address
    from review_app.cart.models import Cart
    from review_app.orders.models import Order


class Customer(Base, TimestampMixin):
    """A buyer account.

    Email is the natural key (case-insensitive on Postgres via CITEXT;
    normalized to lowercase at the model layer everywhere else).
    """

    __tablename__ = "customers"

    # We can't use UUIDPKMixin here because we want to spell the type as
    # `Uuid` (the modern SQLAlchemy 2.0 convention) rather than letting the
    # mixin's plain `mapped_column(primary_key=True)` infer a backend type.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )

    # Email: stored case-insensitively. Postgres uses CITEXT (set in the
    # migration); the Python type is plain str either way.
    email: Mapped[str] = mapped_column(Text, nullable=False)

    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    marketing_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), default=False
    )

    # Legacy fields from leads.json — nullable for new signups.
    legacy_lake_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_state: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance for data migrations.
    created_by_migration: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships — kept lazy to avoid forcing imports; type-checked above.
    addresses: Mapped[list[Address]] = relationship(
        "Address",
        back_populates="customer",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    carts: Mapped[list[Cart]] = relationship(
        "Cart",
        back_populates="customer",
        lazy="selectin",
    )
    orders: Mapped[list[Order]] = relationship(
        "Order",
        back_populates="customer",
        lazy="selectin",
    )

    __table_args__ = (
        Index(
            "customers_email_active_uq",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_customers_stripe_customer_id",
            "stripe_customer_id",
            unique=True,
            postgresql_where=text("stripe_customer_id IS NOT NULL"),
            sqlite_where=text("stripe_customer_id IS NOT NULL"),
        ),
    )

    # ------------------------------------------------------------------
    # Construction / lookup helpers
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        email: str,
        name: str | None = None,
        marketing_opt_in: bool = False,
        legacy_lake_name: str | None = None,
        legacy_state: str | None = None,
        created_by_migration: str | None = None,
    ) -> Customer:
        """Build a new (unpersisted) Customer with normalized email."""
        return cls(
            id=uuid7(),
            email=(email or "").strip().lower(),
            name=(name or None) and name.strip(),
            marketing_opt_in=marketing_opt_in,
            legacy_lake_name=legacy_lake_name,
            legacy_state=legacy_state,
            created_by_migration=created_by_migration,
        )

    @classmethod
    def get_active_by_email(cls, session: Session, email: str) -> Customer | None:
        """Case-insensitive lookup of an active (non-deleted) customer."""
        from sqlalchemy import func, select

        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        stmt = select(cls).where(
            func.lower(cls.email) == normalized,
            cls.deleted_at.is_(None),
        )
        return session.execute(stmt).scalar_one_or_none()

    def __repr__(self) -> str:
        return f"<Customer {self.email!r}>"


__all__ = ["Customer"]
