"""Pure-Python cart business logic — separable from Flask.

All functions take an explicit SQLAlchemy ``Session`` argument and return
Pydantic DTOs (not raw SQLA models) so the unit tests don't need a Flask
app context to exercise the logic.

Boundaries
----------
* Validation lives here (qty>0, qty<=99, SKU is active+in-stock, render_spec
  exists). Routes do request parsing only.
* Snapshot pricing happens here: ``add_item`` reads
  ``prodigi_skus.retail_price_cents`` at insertion time and writes it into
  ``cart_items.unit_price_cents``. Subsequent SKU price changes never affect
  the open cart — the customer sees the price they added.
* Anonymous-vs-customer cart resolution is here: :func:`get_or_create_cart`
  takes either a ``session_token`` (cookie) or a ``customer_id``, never both.
  When a visitor authenticates we call :func:`merge_anonymous_into_customer`
  to fold the cookie cart into the customer's persistent cart.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from review_app.cart.models import Cart, CartItem

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Cart-item quantity hard ceiling. Mirrors the cart_items CHECK constraint
# so route handlers can give a friendly 400 instead of a 500 from the DB.
MAX_QUANTITY_PER_LINE: int = 99


# ---------------------------------------------------------------------------
# Errors — caught by the route layer and translated to HTTP status codes.
# ---------------------------------------------------------------------------
class CartServiceError(Exception):
    """Base class for cart-service business-rule failures."""


class CartNotFoundError(CartServiceError):
    """Raised when the requested cart does not exist (or is closed)."""


class CartItemNotFoundError(CartServiceError):
    """Raised when the requested cart item does not exist in the cart."""


class SkuNotFoundError(CartServiceError):
    """Raised when ``prodigi_sku_internal`` does not match an active SKU."""


class RenderSpecNotFoundError(CartServiceError):
    """Raised when ``render_spec_id`` does not match a known spec."""


class InvalidQuantityError(CartServiceError):
    """Raised when quantity is <= 0 or > :data:`MAX_QUANTITY_PER_LINE`."""


# ---------------------------------------------------------------------------
# DTOs — what callers (routes, tests, JSON serializers) consume.
# ---------------------------------------------------------------------------
class CartItemDTO(BaseModel):
    """Serializable view of a single cart line."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    cart_id: uuid.UUID
    render_spec_id: uuid.UUID | None
    prodigi_sku_internal: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


class CartDTO(BaseModel):
    """Serializable view of a whole cart + computed totals."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    customer_id: uuid.UUID | None
    session_token: str | None
    status: str
    items: list[CartItemDTO] = Field(default_factory=list)
    subtotal_cents: int
    item_count: int


# ---------------------------------------------------------------------------
# DTO converters
# ---------------------------------------------------------------------------
def _item_to_dto(item: CartItem) -> CartItemDTO:
    return CartItemDTO(
        id=item.id,
        cart_id=item.cart_id,
        render_spec_id=item.render_spec_id,
        prodigi_sku_internal=item.prodigi_sku_internal,
        quantity=item.quantity,
        unit_price_cents=item.unit_price_cents,
        line_total_cents=item.line_total_cents,
    )


def cart_to_dto(cart: Cart) -> CartDTO:
    """Materialize a :class:`Cart` (with items eagerly loaded) into a DTO."""
    items_dto = [_item_to_dto(it) for it in (cart.items or [])]
    subtotal = sum(it.line_total_cents for it in items_dto)
    item_count = sum(it.quantity for it in items_dto)
    return CartDTO(
        id=cart.id,
        customer_id=cart.customer_id,
        session_token=cart.session_token,
        status=cart.status,
        items=items_dto,
        subtotal_cents=subtotal,
        item_count=item_count,
    )


# ---------------------------------------------------------------------------
# Cart resolution
# ---------------------------------------------------------------------------
def get_or_create_cart(
    session: Session,
    *,
    customer_id: uuid.UUID | None = None,
    session_token: str | None = None,
) -> Cart:
    """Return the open cart for the given identity, creating one if needed.

    Resolution rules (in priority order):
      1. If ``customer_id`` is provided AND an open cart exists for that
         customer, return it.
      2. Else if ``session_token`` is provided AND an open cart exists for
         that token (and customer_id is NULL), return it.
      3. Else create a new open cart bound to whichever identity was given.

    Caller is responsible for committing.
    """
    from sqlalchemy import select

    if customer_id is None and session_token is None:
        raise ValueError("Either customer_id or session_token must be provided.")

    cart: Cart | None = None
    if customer_id is not None:
        stmt = (
            select(Cart)
            .where(Cart.customer_id == customer_id)
            .where(Cart.status == "open")
            .order_by(Cart.created_at.desc())
            .limit(1)
        )
        cart = session.execute(stmt).scalar_one_or_none()

    if cart is None and session_token is not None:
        stmt = (
            select(Cart)
            .where(Cart.session_token == session_token)
            .where(Cart.customer_id.is_(None))
            .where(Cart.status == "open")
            .order_by(Cart.created_at.desc())
            .limit(1)
        )
        cart = session.execute(stmt).scalar_one_or_none()

    if cart is None:
        cart = Cart(
            customer_id=customer_id,
            session_token=session_token if customer_id is None else None,
            status="open",
        )
        session.add(cart)
        session.flush()
    return cart


def get_cart_by_id(session: Session, cart_id: uuid.UUID) -> Cart:
    """Load a cart by id; raise :class:`CartNotFoundError` if missing."""
    cart = session.get(Cart, cart_id)
    if cart is None:
        raise CartNotFoundError(f"Cart {cart_id!s} not found")
    return cart


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or quantity <= 0 or quantity > MAX_QUANTITY_PER_LINE:
        raise InvalidQuantityError(
            f"quantity must be 1..{MAX_QUANTITY_PER_LINE}, got {quantity!r}"
        )


def _lookup_sku_price(session: Session, internal_sku: str) -> int:
    """Return ``retail_price_cents`` for an active SKU, or raise."""
    from sqlalchemy import select

    from review_app.prodigi.db_models import ProdigiSku

    stmt = select(ProdigiSku).where(ProdigiSku.internal_sku == internal_sku)
    sku = session.execute(stmt).scalar_one_or_none()
    if sku is None:
        raise SkuNotFoundError(f"Unknown internal_sku {internal_sku!r}")
    if not sku.active:
        raise SkuNotFoundError(f"SKU {internal_sku!r} is inactive")
    if sku.retail_price_cents is None:
        raise SkuNotFoundError(
            f"SKU {internal_sku!r} has no retail_price_cents — refresh quote first"
        )
    return int(sku.retail_price_cents)


def _validate_render_spec(session: Session, render_spec_id: uuid.UUID | None) -> None:
    """Confirm the render_spec row exists if one was supplied."""
    if render_spec_id is None:
        return
    from review_app.render.db_models import RenderSpecRow

    spec = session.get(RenderSpecRow, render_spec_id)
    if spec is None:
        raise RenderSpecNotFoundError(f"render_spec_id {render_spec_id!s} not found")


def add_item(
    session: Session,
    cart: Cart,
    *,
    prodigi_sku_internal: str,
    render_spec_id: uuid.UUID | None,
    quantity: int = 1,
) -> CartDTO:
    """Add a SKU+spec to the cart (or bump the quantity if it already exists).

    "Same item" = same ``(render_spec_id, prodigi_sku_internal)`` tuple. Two
    rows for the same SKU+spec collapse into one row with summed quantity.

    Snapshots ``unit_price_cents`` from the SKU at insertion time.

    Returns the cart DTO (with totals) after the mutation.
    Caller is responsible for committing.
    """
    _validate_quantity(quantity)
    _validate_render_spec(session, render_spec_id)
    unit_price_cents = _lookup_sku_price(session, prodigi_sku_internal)

    existing: CartItem | None = next(
        (
            it
            for it in (cart.items or [])
            if it.prodigi_sku_internal == prodigi_sku_internal
            and it.render_spec_id == render_spec_id
        ),
        None,
    )

    if existing is not None:
        new_qty = existing.quantity + quantity
        if new_qty > MAX_QUANTITY_PER_LINE:
            raise InvalidQuantityError(
                f"adding {quantity} would exceed per-line cap "
                f"({MAX_QUANTITY_PER_LINE}); current={existing.quantity}"
            )
        existing.quantity = new_qty
    else:
        item = CartItem(
            cart_id=cart.id,
            render_spec_id=render_spec_id,
            prodigi_sku_internal=prodigi_sku_internal,
            quantity=quantity,
            unit_price_cents=unit_price_cents,
        )
        session.add(item)
        # Append to the in-memory collection so cart_to_dto() sees it without
        # a refresh round-trip.
        if cart.items is None:
            cart.items = [item]
        else:
            cart.items.append(item)

    session.flush()
    return cart_to_dto(cart)


def update_quantity(
    session: Session,
    cart: Cart,
    *,
    item_id: uuid.UUID,
    quantity: int,
) -> CartDTO:
    """Update a line's quantity. ``quantity == 0`` removes the line.

    Caller is responsible for committing.
    """
    if quantity < 0:
        raise InvalidQuantityError(f"quantity must be >= 0, got {quantity!r}")

    item: CartItem | None = next(
        (it for it in (cart.items or []) if it.id == item_id),
        None,
    )
    if item is None:
        raise CartItemNotFoundError(f"cart_item {item_id!s} not in cart {cart.id!s}")

    if quantity == 0:
        session.delete(item)
        if cart.items is not None:
            cart.items = [it for it in cart.items if it.id != item_id]
    else:
        _validate_quantity(quantity)
        item.quantity = quantity

    session.flush()
    return cart_to_dto(cart)


def remove_item(
    session: Session,
    cart: Cart,
    *,
    item_id: uuid.UUID,
) -> CartDTO:
    """Remove a single line from the cart. Caller commits."""
    return update_quantity(session, cart, item_id=item_id, quantity=0)


def compute_totals(cart: Cart) -> dict[str, int]:
    """Pure totals computation. Returns subtotal_cents + item_count.

    Useful for the routes layer + checkout pre-computation. Tax + shipping
    happen at the Stripe Checkout layer (Stripe Tax + dynamic shipping).
    """
    items = list(cart.items or [])
    subtotal = sum(it.line_total_cents for it in items)
    item_count = sum(it.quantity for it in items)
    return {"subtotal_cents": subtotal, "item_count": item_count}


# ---------------------------------------------------------------------------
# Anonymous → customer cart merge
# ---------------------------------------------------------------------------
def merge_anonymous_into_customer(
    session: Session,
    *,
    session_token: str,
    customer_id: uuid.UUID,
) -> Cart | None:
    """Fold the anonymous (cookie) cart into the customer's persistent cart.

    Resolution:
      * If no anonymous cart exists for ``session_token``, return the
        customer's cart (creating one if needed). No work done.
      * If the customer has no open cart, attach the anonymous cart by
        updating ``customer_id`` and clearing ``session_token``.
      * If both exist, walk the anonymous cart's items and merge into the
        customer cart. Conflict rule (same SKU+spec): ``last-modified wins``
        — keep the cart_item with the more recent ``updated_at``. The other
        line is dropped.

    Returns the resulting customer cart (or None if neither side existed —
    edge case for safety).
    Caller commits.
    """
    from sqlalchemy import select

    anon_stmt = (
        select(Cart)
        .where(Cart.session_token == session_token)
        .where(Cart.customer_id.is_(None))
        .where(Cart.status == "open")
        .order_by(Cart.created_at.desc())
        .limit(1)
    )
    anon_cart: Cart | None = session.execute(anon_stmt).scalar_one_or_none()

    customer_stmt = (
        select(Cart)
        .where(Cart.customer_id == customer_id)
        .where(Cart.status == "open")
        .order_by(Cart.created_at.desc())
        .limit(1)
    )
    customer_cart: Cart | None = session.execute(customer_stmt).scalar_one_or_none()

    # Nothing on either side — make a fresh customer cart so callers can rely
    # on the return value.
    if anon_cart is None and customer_cart is None:
        return get_or_create_cart(session, customer_id=customer_id)

    # Anonymous only — promote it to the customer.
    if anon_cart is not None and customer_cart is None:
        anon_cart.customer_id = customer_id
        anon_cart.session_token = None
        session.flush()
        return anon_cart

    # Customer only — nothing to merge.
    if anon_cart is None and customer_cart is not None:
        return customer_cart

    # Both exist — merge anon items into customer cart with last-modified-wins.
    assert anon_cart is not None and customer_cart is not None  # for mypy

    customer_index: dict[tuple[uuid.UUID | None, str], CartItem] = {
        (it.render_spec_id, it.prodigi_sku_internal): it
        for it in (customer_cart.items or [])
    }

    for anon_item in list(anon_cart.items or []):
        key = (anon_item.render_spec_id, anon_item.prodigi_sku_internal)
        existing = customer_index.get(key)
        if existing is None:
            # Move the anon item into the customer cart.
            anon_item.cart_id = customer_cart.id
            customer_cart.items.append(anon_item)
            customer_index[key] = anon_item
            continue

        # Conflict — last-modified wins. Compare updated_at; if the anon row
        # is newer, replace the customer row's quantity + unit_price snapshot.
        if anon_item.updated_at > existing.updated_at:
            existing.quantity = anon_item.quantity
            existing.unit_price_cents = anon_item.unit_price_cents
        # Drop the anon row regardless (we already kept whichever fields won).
        session.delete(anon_item)

    # Mark the anon cart as converted (so it stops showing up as 'open') and
    # blank its session_token so a stale cookie can't re-attach to it.
    anon_cart.status = "abandoned"
    anon_cart.session_token = None
    session.flush()
    return customer_cart


__all__ = [
    "MAX_QUANTITY_PER_LINE",
    "CartDTO",
    "CartItemDTO",
    "CartItemNotFoundError",
    "CartNotFoundError",
    "CartServiceError",
    "InvalidQuantityError",
    "RenderSpecNotFoundError",
    "SkuNotFoundError",
    "add_item",
    "cart_to_dto",
    "compute_totals",
    "get_cart_by_id",
    "get_or_create_cart",
    "merge_anonymous_into_customer",
    "remove_item",
    "update_quantity",
]
