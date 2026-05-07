"""Admin Fulfillment routes — Prodigi connection, webhooks, errors, reprints.

Endpoints registered on ``admin_bp``:

* ``admin.fulfillment_connection`` — ``GET /admin/fulfillment/connection``
* ``admin.fulfillment_webhooks``   — ``GET /admin/fulfillment/webhooks``
* ``admin.fulfillment_errors``     — ``GET /admin/fulfillment/errors``
* ``admin.fulfillment_reprints``   — ``GET/POST /admin/fulfillment/reprints``
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from flask import (
    Response,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import and_, select

from review_app.admin import _session as _admin_session
from review_app.auth.decorators import requires_role

if TYPE_CHECKING:
    from flask import Blueprint
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mask(secret: str | None) -> str:
    """Show only the first 4 + last 4 chars of a secret value."""
    if not secret:
        return ""
    s = secret.strip()
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}{'*' * (len(s) - 8)}{s[-4:]}"


def _classify_error(message: str | None) -> str:
    """Map a Prodigi error message to a coarse error class for filtering."""
    if not message:
        return "other"
    lowered = message.lower()
    if "address" in lowered or "deliverab" in lowered:
        return "address"
    if "image" in lowered or "quality" in lowered or "dpi" in lowered:
        return "image-quality"
    if "rejected" in lowered or "reject" in lowered:
        return "rejection"
    return "other"


def _last_successful_call_ts(session: Session) -> datetime | None:
    """Most-recent successful Prodigi callback timestamp."""
    from review_app.prodigi.db_models import ProdigiCallback

    stmt = (
        select(ProdigiCallback.received_at)
        .where(ProdigiCallback.processed_status == "ok")
        .order_by(ProdigiCallback.received_at.desc())
        .limit(1)
    )
    result = session.execute(stmt).scalar_one_or_none()
    return result if isinstance(result, datetime) else None


def _ping_prodigi() -> tuple[bool, str]:
    """Sandbox ping: hit GET /Products/{KNOWN_SKU} and report ok/err.

    Wrapped in a try/except so callers never crash the page render.
    """
    known_sku = os.environ.get(
        "PRODIGI_PING_SKU", "GLOBAL-CFPM-16x20-PHO-FRA"
    )
    try:
        from review_app.prodigi import get_default_client
    except Exception as exc:
        return False, f"client import failed: {exc}"

    try:
        client = get_default_client()
        client.get_product(known_sku)
        return True, f"OK — fetched {known_sku}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Route registrar
# ---------------------------------------------------------------------------
def register(admin_bp: Blueprint) -> None:
    """Register all Fulfillment views on ``admin_bp``."""

    # -----------------------------------------------------------------
    # GET /admin/fulfillment/connection — Prodigi env, secrets, ping.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/fulfillment/connection",
        methods=["GET", "POST"],
        endpoint="fulfillment_connection",
    )
    @requires_role("admin")
    def fulfillment_connection() -> Response:
        session = _admin_session.get_session()
        try:
            ping_result: dict[str, Any] | None = None
            if request.method == "POST" and request.form.get("action") == "ping":
                ok, msg = _ping_prodigi()
                ping_result = {"ok": ok, "msg": msg}

            api_key = (
                os.environ.get("PRODIGI_API_KEY")
                or os.environ.get("PRODIGI_SANDBOX_API_KEY")
                or ""
            )
            env = (
                os.environ.get("PRODIGI_ENV")
                or os.environ.get("PRODIGI_ENVIRONMENT")
                or "sandbox"
            ).lower()
            callback_url = os.environ.get("PRODIGI_CALLBACK_URL", "")

            html = render_template(
                "admin/fulfillment/connection.html",
                api_key_masked=_mask(api_key),
                env=env,
                callback_url=callback_url,
                last_success_at=_last_successful_call_ts(session),
                ping_result=ping_result,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET /admin/fulfillment/webhooks — webhook log.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/fulfillment/webhooks",
        methods=["GET", "POST"],
        endpoint="fulfillment_webhooks",
    )
    @requires_role("admin", "staff", "viewer")
    def fulfillment_webhooks() -> Response:
        from review_app.prodigi.db_models import ProdigiCallback

        session = _admin_session.get_session()
        try:
            event_type = request.args.get("event_type") or None
            status = request.args.get("status") or None
            stmt = select(ProdigiCallback)
            filters: list[Any] = []
            if event_type:
                filters.append(ProdigiCallback.event_type == event_type)
            if status:
                filters.append(ProdigiCallback.processed_status == status)
            if filters:
                stmt = stmt.where(and_(*filters))
            stmt = stmt.order_by(ProdigiCallback.received_at.desc()).limit(200)
            rows = list(session.execute(stmt).scalars().all())

            html = render_template(
                "admin/fulfillment/webhooks_list.html",
                callbacks=rows,
                filters={
                    "event_type": event_type or "",
                    "status": status or "",
                },
                statuses=("pending", "ok", "error", "retry", "ignored"),
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET /admin/fulfillment/errors — error queue.
    # Source = Orders.status='problem' UNION callbacks.processed_status='error'.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/fulfillment/errors",
        methods=["GET"],
        endpoint="fulfillment_errors",
    )
    @requires_role("admin", "staff")
    def fulfillment_errors() -> Response:
        from review_app.orders.models import Order
        from review_app.prodigi.db_models import ProdigiCallback

        session = _admin_session.get_session()
        try:
            problem_orders = list(
                session.execute(
                    select(Order)
                    .where(Order.status == "problem")
                    .order_by(Order.created_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )
            failed_callbacks = list(
                session.execute(
                    select(ProdigiCallback)
                    .where(ProdigiCallback.processed_status == "error")
                    .order_by(ProdigiCallback.received_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )

            html = render_template(
                "admin/fulfillment/errors_list.html",
                problem_orders=problem_orders,
                failed_callbacks=failed_callbacks,
                classify=_classify_error,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET /admin/fulfillment/reprints — reprint queue + creator.
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/fulfillment/reprints",
        methods=["GET", "POST"],
        endpoint="fulfillment_reprints",
    )
    @requires_role("admin")
    def fulfillment_reprints() -> Response:
        from review_app.orders.models import Order

        session = _admin_session.get_session()
        try:
            error: str | None = None
            if request.method == "POST":
                try:
                    _create_reprint(session, request.form)
                except ValueError as exc:
                    error = str(exc)
                else:
                    _admin_session.close_session_if_owned(session, commit=True)
                    return cast(
                        "Response",
                        redirect(url_for("admin.fulfillment_reprints")),
                    )

            # Reprint inventory: orders with source='reprint'.
            reprints = list(
                session.execute(
                    select(Order)
                    .where(Order.source == "reprint")
                    .order_by(Order.created_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )

            original_id = (
                request.args.get("original_order_id")
                or (request.form.get("original_order_id") if request.form else None)
                or ""
            )
            html = render_template(
                "admin/fulfillment/reprints_list.html",
                reprints=reprints,
                original_order_id=original_id,
                error=error,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # Phase 5b — approve/reject reprint requests (the queue from Phase 5b).
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/fulfillment/reprints/<uuid:reprint_id>/approve",
        methods=["POST"],
        endpoint="fulfillment_reprint_approve",
    )
    @requires_role("admin")
    def fulfillment_reprint_approve(reprint_id: uuid.UUID) -> Response:
        from review_app import audit
        from review_app.refunds.reprints import (
            ReprintRequestError,
            approve_reprint,
        )

        session = _admin_session.get_session()
        try:
            try:
                rr = approve_reprint(
                    session,
                    reprint_id=reprint_id,
                    admin_user_id=_resolve_actor_user_id(),
                )
            except ReprintRequestError as exc:
                _admin_session.close_session_if_owned(session, commit=False)
                flash(f"Could not approve reprint: {exc}", "error")
                return cast(
                    "Response",
                    redirect(url_for("admin.fulfillment_reprints")),
                )
            audit.record(
                session,
                action="reprint_approved",
                target_type="reprint_request",
                target_id=str(reprint_id),
                user_id=str(_resolve_actor_user_id()),
                after={
                    "status": rr.status,
                    "new_prodigi_order_id": rr.new_prodigi_order_id,
                },
            )
            _admin_session.close_session_if_owned(session, commit=True)
            flash("Reprint approved.", "success")
            return cast(
                "Response", redirect(url_for("admin.fulfillment_reprints"))
            )
        except Exception:
            _admin_session.close_session_if_owned(session, commit=False)
            raise

    @admin_bp.route(
        "/fulfillment/reprints/<uuid:reprint_id>/reject",
        methods=["POST"],
        endpoint="fulfillment_reprint_reject",
    )
    @requires_role("admin")
    def fulfillment_reprint_reject(reprint_id: uuid.UUID) -> Response:
        from review_app import audit
        from review_app.refunds.reprints import (
            ReprintRequestError,
            reject_reprint,
        )

        session = _admin_session.get_session()
        try:
            data = request.get_json(silent=True) or request.form
            reason = (data.get("reason") or "").strip()
            if not reason:
                flash("Rejection reason is required.", "error")
                _admin_session.close_session_if_owned(session, commit=False)
                return cast(
                    "Response",
                    redirect(url_for("admin.fulfillment_reprints")),
                )
            try:
                rr = reject_reprint(
                    session,
                    reprint_id=reprint_id,
                    admin_user_id=_resolve_actor_user_id(),
                    reason=reason,
                )
            except ReprintRequestError as exc:
                _admin_session.close_session_if_owned(session, commit=False)
                flash(f"Could not reject reprint: {exc}", "error")
                return cast(
                    "Response",
                    redirect(url_for("admin.fulfillment_reprints")),
                )
            audit.record(
                session,
                action="reprint_rejected",
                target_type="reprint_request",
                target_id=str(reprint_id),
                user_id=str(_resolve_actor_user_id()),
                after={"status": rr.status, "reason": reason},
            )
            _admin_session.close_session_if_owned(session, commit=True)
            flash("Reprint rejected.", "success")
            return cast(
                "Response", redirect(url_for("admin.fulfillment_reprints"))
            )
        except Exception:
            _admin_session.close_session_if_owned(session, commit=False)
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_actor_user_id() -> uuid.UUID:
    """Mirror admin.orders.routes._resolve_actor_user_id (shadow-mode safe)."""
    try:
        from flask_login import current_user

        uid = getattr(current_user, "id", None)
        if isinstance(uid, uuid.UUID):
            return uid
        if isinstance(uid, str):
            try:
                return uuid.UUID(uid)
            except ValueError:
                pass
    except Exception:
        pass
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Reprint creator
# ---------------------------------------------------------------------------
def _create_reprint(session: Session, form: Any) -> uuid.UUID:
    """Spawn a reprint Order from an original.

    The reprint shares the original's customer + shipping address + line
    items but gets ``source='reprint'``, a fresh stripe_payment_intent_id
    of ``"reprint_<original-pi>_<uuid7-hex>"``, and ``status='paid'`` (no
    Stripe charge — cost is internal).

    The cost-tracking is via the ``reason`` text logged into the synthetic
    PI and the order's tax_cents (used as a soft "internal cost" bucket
    until Phase 5 adds a dedicated ``internal_costs`` column).
    """
    from review_app.orders.models import Order, OrderItem

    raw_original = (form.get("original_order_id") or "").strip()
    if not raw_original:
        raise ValueError("original_order_id is required")
    try:
        original_id = uuid.UUID(raw_original)
    except ValueError as exc:
        raise ValueError("original_order_id must be a UUID") from exc

    original = session.get(Order, original_id)
    if original is None:
        raise ValueError(f"order {original_id} not found")

    reason = (form.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason is required")

    cost_cents_raw = (form.get("cost_cents") or "0").strip()
    try:
        cost_cents = max(0, int(cost_cents_raw))
    except ValueError as exc:
        raise ValueError("cost_cents must be an integer") from exc

    new_order = Order(
        customer_id=original.customer_id,
        shipping_address_id=original.shipping_address_id,
        stripe_payment_intent_id=(
            f"reprint_{(original.stripe_payment_intent_id or 'na')[:24]}_"
            f"{uuid.uuid4().hex[:12]}"
        ),
        status="paid",
        subtotal_cents=0,
        shipping_cents=0,
        tax_cents=cost_cents,
        total_cents=cost_cents,
        currency=original.currency,
        source="reprint",
        placed_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    session.add(new_order)
    session.flush()

    for item in original.items or []:
        oi = OrderItem(
            order_id=new_order.id,
            render_spec_id=item.render_spec_id,
            prodigi_sku_internal=item.prodigi_sku_internal,
            quantity=item.quantity,
            unit_price_cents=0,
            line_total_cents=0,
            finish_display=item.finish_display,
            size_inches=item.size_inches,
        )
        session.add(oi)
    session.flush()

    return new_order.id


# Quiet unused-import hints for tools that don't see Jinja runtime use.
_ = (flash,)


__all__ = ["register"]
