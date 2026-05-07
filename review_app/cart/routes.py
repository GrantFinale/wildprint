"""Flask blueprint exposing the cart over JSON + a minimal /cart HTML page.

Routes
------
- ``POST /api/cart/add`` — body: ``{render_spec_id, prodigi_sku_internal, quantity}``
- ``GET  /api/cart`` — current cart contents + totals
- ``POST /api/cart/items/<item_id>/update`` — body: ``{quantity}`` (0 deletes)
- ``POST /api/cart/items/<item_id>/remove``
- ``GET  /cart`` — minimal HTML page rendering the cart

Identity resolution
-------------------
A cart is owned by either ``customer_id`` (Flask-Login current_user) or a
``session_token`` cookie ("wp_cart_session"). On login, the parallel auth
flow can call :func:`review_app.cart.service.merge_anonymous_into_customer`
to fold the anonymous cart into the customer cart.

The cookie is HttpOnly + SameSite=Lax + Secure-when-https. It contains an
opaque random token (32 bytes URL-safe base64). It is NOT a Flask session
field — we use a dedicated cookie so the cart survives session-cookie
rotation (e.g. user logs out then back in).
"""
from __future__ import annotations

import secrets
import uuid
from typing import TYPE_CHECKING, Any

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
)

from review_app.cart import service as cart_service
from review_app.cart.models import Cart
from review_app.limits import cart_limit as _cart_limit

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


cart_bp = Blueprint("cart", __name__)


# Cookie used to resolve anonymous carts across requests.
SESSION_COOKIE_NAME = "wp_cart_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_session() -> Session:
    """Return a SQLAlchemy session bound to this request.

    Prefers a Flask-extension-style ``g.db`` if the legacy app exposes one;
    otherwise falls back to ``review_app.db.get_session_factory()()``. The
    caller is responsible for committing/closing in either case.
    """
    from flask import g

    existing = getattr(g, "db", None)
    if existing is not None:
        return existing  # type: ignore[no-any-return]

    from review_app.db import get_session_factory

    session = get_session_factory()()
    g.db = session
    g.db_owned_by_request = True
    return session


def _close_session_if_owned(session: Session, commit: bool) -> None:
    """Commit/rollback + close a session that this request opened."""
    from flask import g

    if not getattr(g, "db_owned_by_request", False):
        return
    try:
        if commit:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()
        g.db = None
        g.db_owned_by_request = False


def _current_customer_id() -> uuid.UUID | None:
    """Best-effort lookup of the logged-in customer id.

    Returns ``None`` for anonymous visitors. Decoupled from Flask-Login so
    tests can monkey-patch via ``flask.g.customer_id`` instead of mocking
    the entire login chain.
    """
    from flask import g

    cid = getattr(g, "customer_id", None)
    if isinstance(cid, uuid.UUID):
        return cid
    if isinstance(cid, str):
        try:
            return uuid.UUID(cid)
        except ValueError:
            return None
    return None


def _ensure_session_token(response: Response | None = None) -> str:
    """Read or mint the anonymous cart cookie. Sets it on ``response`` if given."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        return token
    token = secrets.token_urlsafe(32)
    if response is not None:
        secure = request.is_secure
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=secure,
        )
    return token


def _resolve_cart(session: Session, *, mint_cookie: bool, response: Response | None = None) -> tuple[Cart, str | None]:
    """Find or create the cart for this request. Returns (cart, session_token).

    ``session_token`` is None when the visitor is logged in (we don't carry
    a cookie cart in that case).
    """
    customer_id = _current_customer_id()
    if customer_id is not None:
        cart = cart_service.get_or_create_cart(session, customer_id=customer_id)
        return cart, None

    token = _ensure_session_token(response if mint_cookie else None)
    cart = cart_service.get_or_create_cart(session, session_token=token)
    return cart, token


def _serialize_dto(dto: cart_service.CartDTO) -> dict[str, Any]:
    """Pydantic dump → JSON-friendly dict (UUIDs → strings)."""
    return dto.model_dump(mode="json")


def _error_response(exc: cart_service.CartServiceError) -> Response:
    """Translate a service-layer exception into a JSON 400/404 response."""
    if isinstance(exc, cart_service.CartNotFoundError | cart_service.CartItemNotFoundError | cart_service.RenderSpecNotFoundError | cart_service.SkuNotFoundError):
        status = 404
    else:
        status = 400
    payload: dict[str, Any] = {"error": str(exc), "type": type(exc).__name__}
    return make_response(jsonify(payload), status)


def _parse_uuid_field(value: Any, field_name: str) -> uuid.UUID:
    """Parse a UUID JSON field; raise ValueError with a friendly message."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name!r} must be a UUID string")
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name!r} is not a valid UUID: {value!r}") from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@cart_bp.route("/api/cart/add", methods=["POST"])
@_cart_limit()
def api_cart_add() -> Response:
    """Add an item to the current cart. Returns the updated cart DTO."""
    body: Any = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return make_response(jsonify({"error": "JSON body must be an object"}), 400)

    try:
        sku = body.get("prodigi_sku_internal")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("'prodigi_sku_internal' is required")
        sku = sku.strip()

        render_spec_id_raw = body.get("render_spec_id")
        render_spec_id: uuid.UUID | None = (
            None
            if render_spec_id_raw in (None, "")
            else _parse_uuid_field(render_spec_id_raw, "render_spec_id")
        )

        quantity_raw = body.get("quantity", 1)
        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'quantity' must be an integer, got {quantity_raw!r}") from exc
    except ValueError as exc:
        return make_response(jsonify({"error": str(exc)}), 400)

    response = make_response("")
    session = _get_session()
    committed = False
    try:
        cart, _ = _resolve_cart(session, mint_cookie=True, response=response)
        try:
            dto = cart_service.add_item(
                session,
                cart,
                prodigi_sku_internal=sku,
                render_spec_id=render_spec_id,
                quantity=quantity,
            )
        except cart_service.CartServiceError as exc:
            return _error_response(exc)

        # Build the JSON body and stitch it onto the cookie-bearing response.
        body_payload = jsonify({"cart": _serialize_dto(dto)})
        response.set_data(body_payload.get_data())
        response.mimetype = body_payload.mimetype or "application/json"
        response.status_code = 200
        committed = True
        return response
    finally:
        _close_session_if_owned(session, commit=committed)


@cart_bp.route("/api/cart", methods=["GET"])
def api_cart_get() -> Response:
    """Return the current cart contents + totals."""
    response = make_response("")
    session = _get_session()
    committed = False
    try:
        cart, _ = _resolve_cart(session, mint_cookie=False, response=response)
        dto = cart_service.cart_to_dto(cart)
        body_payload = jsonify({"cart": _serialize_dto(dto)})
        response.set_data(body_payload.get_data())
        response.mimetype = body_payload.mimetype or "application/json"
        response.status_code = 200
        committed = True
        return response
    finally:
        _close_session_if_owned(session, commit=committed)


@cart_bp.route("/api/cart/items/<item_id>/update", methods=["POST"])
@_cart_limit()
def api_cart_item_update(item_id: str) -> Response:
    """Update a line's quantity. body: ``{quantity: int}`` (0 deletes)."""
    body: Any = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return make_response(jsonify({"error": "JSON body must be an object"}), 400)

    try:
        item_uuid = _parse_uuid_field(item_id, "item_id")
        try:
            quantity = int(body.get("quantity"))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("'quantity' must be an integer") from exc
    except ValueError as exc:
        return make_response(jsonify({"error": str(exc)}), 400)

    session = _get_session()
    committed = False
    try:
        cart, _ = _resolve_cart(session, mint_cookie=False)
        try:
            dto = cart_service.update_quantity(
                session, cart, item_id=item_uuid, quantity=quantity
            )
        except cart_service.CartServiceError as exc:
            return _error_response(exc)
        committed = True
        return jsonify({"cart": _serialize_dto(dto)})
    finally:
        _close_session_if_owned(session, commit=committed)


@cart_bp.route("/api/cart/items/<item_id>/remove", methods=["POST"])
@_cart_limit()
def api_cart_item_remove(item_id: str) -> Response:
    """Remove a single line from the current cart."""
    try:
        item_uuid = _parse_uuid_field(item_id, "item_id")
    except ValueError as exc:
        return make_response(jsonify({"error": str(exc)}), 400)

    session = _get_session()
    committed = False
    try:
        cart, _ = _resolve_cart(session, mint_cookie=False)
        try:
            dto = cart_service.remove_item(session, cart, item_id=item_uuid)
        except cart_service.CartServiceError as exc:
            return _error_response(exc)
        committed = True
        return jsonify({"cart": _serialize_dto(dto)})
    finally:
        _close_session_if_owned(session, commit=committed)


@cart_bp.route("/cart", methods=["GET"])
def cart_page() -> Response | str:
    """Minimal HTML cart page with quantity controls + checkout CTA."""
    response = make_response("")
    session = _get_session()
    committed = False
    try:
        cart, _ = _resolve_cart(session, mint_cookie=True, response=response)
        dto = cart_service.cart_to_dto(cart)
        committed = True
        try:
            html = render_template("cart/cart.html", cart=dto)
        except Exception:
            current_app.logger.exception("Failed to render cart/cart.html")
            html = _fallback_cart_html(dto)
        response.set_data(html)
        response.mimetype = "text/html"
        response.status_code = 200
        return response
    finally:
        _close_session_if_owned(session, commit=committed)


def _fallback_cart_html(dto: cart_service.CartDTO) -> str:
    """Last-resort HTML when the template can't render. Tests may rely on this."""
    rows = "".join(
        f"<tr><td>{it.prodigi_sku_internal}</td>"
        f"<td>{it.quantity}</td>"
        f"<td>${it.unit_price_cents / 100:.2f}</td>"
        f"<td>${it.line_total_cents / 100:.2f}</td></tr>"
        for it in dto.items
    )
    return (
        "<!doctype html><html><head><title>Cart</title></head><body>"
        f"<h1>Your cart</h1><table>{rows}</table>"
        f"<p>Subtotal: ${dto.subtotal_cents / 100:.2f}</p>"
        '<form method="post" action="/api/checkout/start">'
        '<button type="submit">Proceed to Checkout</button>'
        "</form></body></html>"
    )


__all__ = ["SESSION_COOKIE_NAME", "cart_bp"]
