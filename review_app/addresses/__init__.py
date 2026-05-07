"""Addresses module — shipping addresses + Smarty validation.

Phase 3a:

* Models live in :mod:`review_app.addresses.models`.
* Smarty client lives in :mod:`review_app.addresses.smarty`.
* :func:`validate_and_persist` is the orchestration entrypoint that the
  cart/checkout flow calls before letting an order proceed.

Design choice (flagged in commit message):
The cart/checkout flow REFUSES to proceed when an address is non-deliverable.
We always persist the Address row with ``validation_provider='smarty'``,
``dpv_match_code`` set, and ``validation_response`` saved. ``validated_at``
is set to ``now()`` only when the DPV code is in ``DELIVERABLE_DPV_CODES``.
This preserves an audit record of the rejected attempt while preventing
accidental shipment to un-deliverable addresses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from review_app.addresses.models import (
    DELIVERABLE_DPV_CODES,
    Address,
)
from review_app.addresses.smarty import (
    SmartyError,
    SmartyResult,
    verify_address,
)

if TYPE_CHECKING:
    import uuid

    from flask import Flask
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AddressInput:
    """Plain input bag for :func:`validate_and_persist`.

    Kept frozen + dataclass-typed so the cart/checkout layer can construct
    these from form data without coupling to SQLAlchemy or pydantic types.
    """

    name: str | None
    line1: str
    line2: str | None
    city: str
    state: str
    zip_code: str
    country: str = "US"
    phone: str | None = None
    is_default: bool = False


def validate_and_persist(
    session: Session,
    customer_id: uuid.UUID,
    address_input: AddressInput,
) -> Address:
    """Verify an address with Smarty, then persist a row.

    Always inserts a row (so we have audit history). ``validated_at`` is set
    only when Smarty reports the address as deliverable; non-deliverable
    rows are saved with ``validated_at=None`` and the cart/checkout layer
    inspects :attr:`Address.is_deliverable` (or ``validated_at IS NOT NULL``)
    before proceeding.

    On Smarty transport failure (network/4xx/5xx) the exception propagates
    — the caller decides whether to retry or surface to the user.

    Returns the persisted (flushed) Address. Caller commits.
    """
    result: SmartyResult = verify_address(
        line1=address_input.line1,
        line2=address_input.line2,
        city=address_input.city,
        state=address_input.state,
        zip_code=address_input.zip_code,
        country=address_input.country,
    )

    address = Address(
        customer_id=customer_id,
        name=address_input.name,
        # Prefer Smarty's standardized line if it gave us one.
        line1=(result.standardized_line1 or address_input.line1),
        line2=address_input.line2,
        city=(result.standardized_city or address_input.city),
        state=(result.standardized_state or address_input.state).upper(),
        zip=(result.standardized_zip or address_input.zip_code),
        country=address_input.country.upper(),
        phone=address_input.phone,
        is_default=address_input.is_default,
        validation_provider="smarty",
        validation_response=result.raw_response,
        dpv_match_code=result.dpv_match_code or None,
        validated_at=(
            datetime.now(UTC) if result.is_deliverable else None
        ),
    )
    session.add(address)
    session.flush()
    return address


def init_app(app: Flask) -> None:
    """No-op wiring stub. Implementations land in later phases."""
    return None


__all__ = [
    "DELIVERABLE_DPV_CODES",
    "Address",
    "AddressInput",
    "SmartyError",
    "SmartyResult",
    "init_app",
    "validate_and_persist",
    "verify_address",
]
