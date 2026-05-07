"""Address ORM model.

Mirrors ``alembic/versions/0009_addresses.py``.

An address is "validated" when ``validated_at IS NOT NULL`` — meaning Smarty
returned a usable ``dpv_match_code``. The cart/checkout flow refuses to
proceed unless ``is_deliverable`` (q.v.) is True; see
``review_app/addresses/__init__.py`` and the validate-and-persist orchestration
notes there.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_app.db.base import Base, TimestampMixin, uuid7

if TYPE_CHECKING:
    from review_app.customers.models import Customer


# DPV codes that indicate the address is good to ship to. See:
# https://www.smarty.com/docs/cloud/us-street-api#dpv_match_code
DELIVERABLE_DPV_CODES: frozenset[str] = frozenset({"Y", "S", "D"})


def _json_col() -> Any:
    return JSON().with_variant(JSONB(), "postgresql")


class Address(Base, TimestampMixin):
    """A shipping address belonging to a Customer."""

    __tablename__ = "addresses"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "customers.id",
            ondelete="CASCADE",
            name="fk_addresses_customer_id_customers",
        ),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    line1: Mapped[str] = mapped_column(Text, nullable=False)
    line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    zip: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'US'"), default="US"
    )
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), default=False
    )

    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validation_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_response: Mapped[dict[str, Any] | None] = mapped_column(
        _json_col(), nullable=True
    )
    dpv_match_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="addresses"
    )

    __table_args__ = (
        CheckConstraint(
            "length(state) = 2",
            name="ck_addresses_state_two_chars",
        ),
        CheckConstraint(
            "length(country) = 2",
            name="ck_addresses_country_two_chars",
        ),
        Index("ix_addresses_customer_id", "customer_id"),
        Index(
            "addresses_one_default_per_customer_uq",
            "customer_id",
            unique=True,
            postgresql_where=text("is_default = true AND deleted_at IS NULL"),
            sqlite_where=text("is_default = 1 AND deleted_at IS NULL"),
        ),
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def is_deliverable(self) -> bool:
        """True if Smarty validated this address as USPS-deliverable.

        Returns False if validation hasn't run, the DPV code is missing, or
        the DPV code indicates non-deliverability ('N', etc.).
        """
        if self.validated_at is None:
            return False
        return (self.dpv_match_code or "") in DELIVERABLE_DPV_CODES

    def __repr__(self) -> str:
        return (
            f"<Address {self.line1!r}, {self.city}, {self.state} {self.zip} "
            f"deliverable={self.is_deliverable}>"
        )


__all__ = ["DELIVERABLE_DPV_CODES", "Address"]
