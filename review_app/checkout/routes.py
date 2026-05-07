"""Flask blueprint exposing the new physical-product checkout entry.

Adds `POST /api/checkout/start` alongside the legacy `POST /api/create-checkout-session`
in `review_app.app`. The legacy endpoint stays for the $49 unlock; this one
owns physical orders.
"""
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, jsonify, make_response, request

from review_app.cart import service as cart_service
from review_app.checkout import stripe_client

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


checkout_bp = Blueprint("checkout", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_session() -> Session:
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


def _absolute_url(path: str) -> str:
    """Return an absolute URL for a relative path using the request host."""
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return f"{base}{path}"
    return request.url_root.rstrip("/") + path


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@checkout_bp.route("/api/checkout/start", methods=["POST"])
def api_checkout_start() -> Response:
    """Begin a Stripe-hosted checkout for the current cart.

    Body:
      - cart_id: UUID (required)
      - shipping_address_id: UUID (required, must be deliverable)
      - customer_email: str (required)
      - marketing_opt_in: bool (optional)

    Returns the Stripe Checkout Session URL the front-end redirects to.
    """
    body: Any = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return make_response(jsonify({"error": "JSON body must be an object"}), 400)

    try:
        cart_id_raw = body.get("cart_id")
        if not isinstance(cart_id_raw, str) or not cart_id_raw:
            raise ValueError("'cart_id' is required")
        cart_id = uuid.UUID(cart_id_raw)

        addr_raw = body.get("shipping_address_id")
        if not isinstance(addr_raw, str) or not addr_raw:
            raise ValueError("'shipping_address_id' is required")
        shipping_address_id = uuid.UUID(addr_raw)

        email = body.get("customer_email")
        if not isinstance(email, str) or "@" not in email:
            raise ValueError("'customer_email' is required and must be an email")
        email = email.strip().lower()

        marketing_opt_in = bool(body.get("marketing_opt_in", False))
    except (TypeError, ValueError) as exc:
        return make_response(jsonify({"error": str(exc)}), 400)

    session = _get_session()
    committed = False
    try:
        # Validate shipping address — must be deliverable per Smarty.
        from review_app.addresses.models import Address

        address = session.get(Address, shipping_address_id)
        if address is None:
            return make_response(
                jsonify({"error": "shipping_address_id not found"}), 404
            )
        if not address.is_deliverable:
            return make_response(
                jsonify(
                    {
                        "error": "shipping address is not deliverable",
                        "dpv_match_code": address.dpv_match_code,
                    }
                ),
                400,
            )

        try:
            cart = cart_service.get_cart_by_id(session, cart_id)
        except cart_service.CartNotFoundError:
            return make_response(jsonify({"error": "cart not found"}), 404)

        if cart.status != "open":
            return make_response(
                jsonify({"error": f"cart is not open (status={cart.status})"}), 400
            )
        cart_dto = cart_service.cart_to_dto(cart)
        if cart_dto.item_count == 0:
            return make_response(jsonify({"error": "cart is empty"}), 400)

        success_url = _absolute_url("/checkout/success/v2") + "?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = _absolute_url("/cart")

        try:
            session_obj = stripe_client.create_checkout_session_for_cart(
                cart=cart_dto,
                customer_email=email,
                success_url=success_url,
                cancel_url=cancel_url,
                shipping_address_id=str(shipping_address_id),
                metadata={
                    "marketing_opt_in": "1" if marketing_opt_in else "0",
                    "customer_email_lower": email,
                },
            )
        except stripe_client.StripeNotConfiguredError as exc:
            return make_response(jsonify({"error": str(exc)}), 503)
        except Exception as exc:
            return make_response(
                jsonify({"error": "Stripe checkout session creation failed", "detail": str(exc)}),
                502,
            )

        committed = True
        return jsonify(
            {
                "checkout_session_id": session_obj.get("id"),
                "url": session_obj.get("url"),
                "cart_id": str(cart_id),
            }
        )
    finally:
        _close_session_if_owned(session, commit=committed)


__all__ = ["checkout_bp"]
