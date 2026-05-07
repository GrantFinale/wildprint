"""Thin typed wrapper around the Stripe SDK.

Centralizes:

* API key resolution from ``STRIPE_SECRET_KEY``.
* The 3 verbs Phase 3b needs:
    - :func:`create_checkout_session_for_cart`
    - :func:`create_payment_intent` (for ad-hoc charges, used by tests)
    - :func:`create_refund`
    - :func:`retrieve_event` (used by the webhook handler when it wants the
      typed event object)
* :func:`construct_event_from_payload` — wraps ``stripe.Webhook.construct_event``
  so the webhook handler doesn't import ``stripe`` directly.

Tests mock these functions instead of stubbing the entire ``stripe`` package.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from review_app.cart.service import CartDTO


def _to_dict(obj: Any) -> dict[str, Any]:
    """Coerce a Stripe SDK object (StripeObject) to a plain dict.

    The SDK's typeshed stubs don't expose ``to_dict``, so we cast.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return cast(dict[str, Any], obj.to_dict())
    # Fallback: pull __dict__ keys (Stripe objects are dict-like).
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class StripeNotConfiguredError(RuntimeError):
    """Raised when ``STRIPE_SECRET_KEY`` is missing at call time."""


class StripeSignatureError(ValueError):
    """Raised when a webhook signature fails verification."""


# ---------------------------------------------------------------------------
# API key resolution (lazy — never raise at import)
# ---------------------------------------------------------------------------
def _ensure_api_key() -> None:
    """Configure the SDK with ``STRIPE_SECRET_KEY``. Raise if missing."""
    api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not api_key:
        raise StripeNotConfiguredError(
            "STRIPE_SECRET_KEY env var is not set; cannot call Stripe."
        )
    import stripe  # local import — avoids hard requirement at module import

    stripe.api_key = api_key


def _webhook_secret() -> str:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET_V2", "").strip()
    if not secret:
        # Fall back to the legacy var so the v2 endpoint still works in dev
        # environments that only have the original secret configured.
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise StripeNotConfiguredError(
            "STRIPE_WEBHOOK_SECRET_V2 (or STRIPE_WEBHOOK_SECRET) is required "
            "for the v2 webhook handler."
        )
    return secret


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def create_checkout_session_for_cart(
    *,
    cart: CartDTO,
    customer_email: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str] | None = None,
    shipping_address_id: str | None = None,
    automatic_tax: bool = False,
) -> dict[str, Any]:
    """Create a Stripe Checkout Session for a multi-line physical cart.

    Returns the raw stripe Session object (cast to ``dict``) so callers can
    serialize it to JSON for the front-end without coupling to ``stripe.api_resources``.

    When ``automatic_tax`` is True, passes ``automatic_tax={'enabled': True}``
    to Stripe — Stripe handles tax registration + calculation server-side.
    The opt-in is gated by Phase 5a's ``STRIPE_TAX_ENABLED`` env flag at the
    caller (route layer); this function takes a plain boolean so the
    stripe_client module stays env-flag agnostic + easy to mock.
    """
    _ensure_api_key()
    import stripe

    if cart.item_count <= 0:
        raise ValueError("Cannot create a Stripe session for an empty cart")

    line_items: list[dict[str, Any]] = []
    for item in cart.items:
        line_items.append(
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": item.unit_price_cents,
                    "product_data": {
                        "name": item.prodigi_sku_internal,
                        "metadata": {
                            "cart_item_id": str(item.id),
                            "render_spec_id": (
                                str(item.render_spec_id)
                                if item.render_spec_id
                                else ""
                            ),
                            "prodigi_sku_internal": item.prodigi_sku_internal,
                        },
                    },
                },
                "quantity": item.quantity,
            }
        )

    full_metadata: dict[str, str] = {
        "cart_id": str(cart.id),
        "wildprint_flow": "physical_v1",
    }
    if shipping_address_id:
        full_metadata["shipping_address_id"] = shipping_address_id
    if metadata:
        full_metadata.update(metadata)

    session_kwargs: dict[str, Any] = {
        "mode": "payment",
        # cast: typeshed expects a TypedDict per line item, but the SDK
        # accepts plain dicts at runtime. The fields are right (price_data
        # nested with currency/unit_amount/product_data) — just not the
        # name that mypy expects.
        "line_items": cast(Any, line_items),
        "customer_email": customer_email,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": full_metadata,
        "payment_intent_data": {"metadata": full_metadata},
        # Bundle digital free with physical (decision #3 in integration-plan.md):
        # we don't add a separate $49 line — the digital download ships with
        # the printed poster, conceptually "in the box".
        "allow_promotion_codes": True,
    }
    if automatic_tax:
        # Phase 5a: defer tax registration / reporting to Stripe Tax.
        # The line item ``price_data`` above does NOT have ``tax_behavior``
        # set, so Stripe treats unit_amount as exclusive — its default for
        # automatic_tax. Also collect the billing address so Stripe can
        # determine the customer's tax jurisdiction even if the shipping
        # address is supplied separately.
        session_kwargs["automatic_tax"] = {"enabled": True}
        session_kwargs["billing_address_collection"] = "required"

    session = stripe.checkout.Session.create(**session_kwargs)
    return _to_dict(session)


def create_payment_intent(
    *,
    amount_cents: int,
    currency: str = "usd",
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a Stripe PaymentIntent (used by admin/manual charges)."""
    _ensure_api_key()
    import stripe

    pi = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency=currency,
        metadata=metadata or {},
    )
    return _to_dict(pi)


def create_refund(
    *,
    payment_intent_id: str,
    amount_cents: int | None = None,
    reason: str | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a Stripe Refund. ``amount_cents=None`` means full refund."""
    _ensure_api_key()
    import stripe

    kwargs: dict[str, Any] = {
        "payment_intent": payment_intent_id,
        "metadata": metadata or {},
    }
    if amount_cents is not None:
        kwargs["amount"] = amount_cents
    # Stripe allows: 'duplicate', 'fraudulent', 'requested_by_customer'.
    if reason and reason in {"duplicate", "fraudulent", "requested_by_customer"}:
        kwargs["reason"] = reason

    refund = stripe.Refund.create(**kwargs)
    return _to_dict(refund)


def retrieve_event(event_id: str) -> dict[str, Any]:
    """Re-fetch an event via Stripe's API (for forensics / replay)."""
    _ensure_api_key()
    import stripe

    event = stripe.Event.retrieve(event_id)
    return _to_dict(event)


def construct_event_from_payload(payload: bytes, signature: str) -> dict[str, Any]:
    """Verify the Stripe-Signature header and return the parsed event dict.

    Raises :class:`StripeSignatureError` on invalid signature.
    """
    secret = _webhook_secret()
    import stripe

    try:
        # construct_event isn't typed in the SDK stubs.
        event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
            payload, signature, secret
        )
    except Exception as exc:  # SignatureVerificationError or ValueError
        raise StripeSignatureError(f"Stripe signature verification failed: {exc}") from exc

    return _to_dict(event)


__all__ = [
    "StripeNotConfiguredError",
    "StripeSignatureError",
    "construct_event_from_payload",
    "create_checkout_session_for_cart",
    "create_payment_intent",
    "create_refund",
    "retrieve_event",
]
