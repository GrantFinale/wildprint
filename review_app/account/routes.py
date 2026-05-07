"""Customer-facing /account/* routes.

* Magic-link login + verify + logout
* Overview, orders list/detail, addresses CRUD, profile

The blueprint owns its own ``before_request`` hook to populate
``g.current_customer`` from ``session['customer_id']``. Authenticated routes
are guarded with :func:`review_app.account.decorators.requires_customer`.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select

from review_app.account.decorators import requires_customer

if TYPE_CHECKING:
    from review_app.customers.models import Customer


account_bp = Blueprint(
    "account",
    __name__,
    url_prefix="/account",
    template_folder="../templates/account",
)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Session helpers (mirror admin/_session.py for this blueprint)
# ---------------------------------------------------------------------------
def _get_session() -> Any:
    existing = getattr(g, "db", None)
    if existing is not None:
        return existing
    from review_app.db import get_session_factory

    s = get_session_factory()()
    g.db = s
    g.db_owned_by_request = True
    return s


def _close_session(commit: bool) -> None:
    s = getattr(g, "db", None)
    if s is None or not getattr(g, "db_owned_by_request", False):
        return
    try:
        if commit:
            s.commit()
        else:
            s.rollback()
    finally:
        s.close()
        g.db = None
        g.db_owned_by_request = False


# ---------------------------------------------------------------------------
# before/after request — populate g.current_customer
# ---------------------------------------------------------------------------
@account_bp.before_request
def _load_current_customer() -> None:
    """Populate ``g.current_customer`` from the session cookie."""
    g.current_customer = None
    raw = session.get("customer_id")
    if not raw:
        return
    try:
        customer_id = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        session.pop("customer_id", None)
        return
    from review_app.customers.models import Customer

    s = _get_session()
    cust = s.get(Customer, customer_id)
    if cust is None or cust.deleted_at is not None:
        session.pop("customer_id", None)
        return
    g.current_customer = cust


@account_bp.teardown_request
def _teardown(_exc: BaseException | None) -> None:
    _close_session(commit=_exc is None)


# ---------------------------------------------------------------------------
# Auth: GET/POST /account/login + /verify + /logout
# ---------------------------------------------------------------------------
@account_bp.route("/login", methods=["GET", "POST"])
def login() -> Response:
    next_url = request.args.get("next") or request.form.get("next") or ""

    if request.method == "GET":
        return make_response(
            render_template(
                "account/login.html",
                next_url=next_url,
                sent=False,
                error=None,
            )
        )

    email = (request.form.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return make_response(
            render_template(
                "account/login.html",
                next_url=next_url,
                sent=False,
                error="Please enter a valid email address.",
            ),
            400,
        )

    from review_app.account.auth import issue_token, send_magic_link
    from review_app.customers.models import Customer

    s = _get_session()
    cust = Customer.get_active_by_email(s, email)
    if cust is None:
        cust = Customer.create(email=email)
        s.add(cust)
        s.flush()

    secret_key = str(current_app.config.get("SECRET_KEY") or current_app.secret_key or "")
    if not secret_key:
        return make_response(
            render_template(
                "account/login.html",
                next_url=next_url,
                sent=False,
                error="server is misconfigured (no SECRET_KEY)",
            ),
            500,
        )

    token = issue_token(
        s,
        customer_id=cust.id,
        secret_key=secret_key,
        ip_address=request.remote_addr,
    )
    base_url = request.url_root
    send_magic_link(s, customer_email=cust.email, token=token, base_url=base_url)

    return make_response(
        render_template(
            "account/login.html",
            next_url=next_url,
            sent=True,
            error=None,
        )
    )


@account_bp.route("/login/verify", methods=["GET"])
def login_verify() -> Response:
    token = (request.args.get("token") or "").strip()
    next_url = (request.args.get("next") or "").strip() or url_for("account.overview")
    if not token:
        return cast(
            "Response",
            redirect(url_for("account.login", next=next_url)),
        )

    from review_app.account.auth import verify_token

    s = _get_session()
    secret_key = str(current_app.config.get("SECRET_KEY") or current_app.secret_key or "")
    customer_id = verify_token(s, token=token, secret_key=secret_key)
    if customer_id is None:
        flash("Sign-in link is invalid or has expired. Please request a new one.", "error")
        return cast(
            "Response",
            redirect(url_for("account.login", next=next_url)),
        )

    session["customer_id"] = str(customer_id)
    return cast("Response", redirect(next_url))


@account_bp.route("/logout", methods=["GET", "POST"])
def logout() -> Response:
    session.pop("customer_id", None)
    return cast("Response", redirect(url_for("account.login")))


# ---------------------------------------------------------------------------
# Overview + orders list/detail
# ---------------------------------------------------------------------------
@account_bp.route("/", methods=["GET"], strict_slashes=False)
@requires_customer
def overview() -> Response:
    from review_app.orders.models import Order

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    recent = list(
        s.execute(
            select(Order)
            .where(Order.customer_id == customer.id)
            .order_by(Order.created_at.desc())
            .limit(5)
        )
        .scalars()
        .all()
    )
    default_address = next(
        (a for a in (customer.addresses or []) if a.is_default), None
    )
    return make_response(
        render_template(
            "account/overview.html",
            customer=customer,
            recent_orders=recent,
            default_address=default_address,
        )
    )


@account_bp.route("/orders", methods=["GET"])
@requires_customer
def orders_list() -> Response:
    from review_app.orders.models import Order

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    rows = list(
        s.execute(
            select(Order)
            .where(Order.customer_id == customer.id)
            .order_by(Order.created_at.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return make_response(
        render_template(
            "account/orders_list.html",
            customer=customer,
            orders=rows,
        )
    )


@account_bp.route("/orders/<uuid:order_id>", methods=["GET"])
@requires_customer
def orders_detail(order_id: uuid.UUID) -> Response:
    from review_app.orders.models import Order

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    order = s.get(Order, order_id)
    if order is None or order.customer_id != customer.id:
        return make_response(
            render_template("account/order_detail.html", order=None, not_found=True),
            404,
        )

    items = list(order.items or [])
    can_request_reprint = _can_request_reprint(s, order)
    return make_response(
        render_template(
            "account/order_detail.html",
            order=order,
            items=items,
            customer=customer,
            shipping=order.shipping_address,
            can_request_reprint=can_request_reprint,
            not_found=False,
        )
    )


@account_bp.route("/orders/<uuid:order_id>/reprint", methods=["POST"])
@requires_customer
def orders_reprint(order_id: uuid.UUID) -> Response:
    from review_app.orders.models import Order
    from review_app.refunds.reprints import (
        ReprintRequestError,
        request_reprint,
    )

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    order = s.get(Order, order_id)
    if order is None or order.customer_id != customer.id:
        flash("Order not found.", "error")
        return cast(
            "Response", redirect(url_for("account.orders_list"))
        )

    if not _can_request_reprint(s, order):
        flash("This order isn't eligible for a reprint.", "error")
        return cast(
            "Response",
            redirect(url_for("account.orders_detail", order_id=str(order_id))),
        )

    reason = (request.form.get("reason") or "").strip() or None
    try:
        request_reprint(
            s,
            order_id=order.id,
            customer_id=customer.id,
            reason=reason,
            line_item_ids=None,
            requested_by_role="customer",
        )
    except ReprintRequestError as exc:
        flash(f"Could not submit reprint request: {exc}", "error")
        return cast(
            "Response",
            redirect(url_for("account.orders_detail", order_id=str(order_id))),
        )

    flash("Reprint request submitted. We'll be in touch soon.", "success")
    return cast(
        "Response",
        redirect(url_for("account.orders_detail", order_id=str(order_id))),
    )


def _can_request_reprint(s: Any, order: Any) -> bool:
    """Eligibility: delivered, < 30 days old, no existing pending/approved reprint."""
    from review_app.refunds.reprints_models import ReprintRequest

    if order.status != "delivered":
        return False
    delivered = order.delivered_at
    if delivered is None:
        return False
    if delivered.tzinfo is None:
        delivered = delivered.replace(tzinfo=UTC)
    if datetime.now(UTC) - delivered > timedelta(days=30):
        return False
    existing = s.execute(
        select(ReprintRequest)
        .where(ReprintRequest.order_id == order.id)
        .where(ReprintRequest.status.in_(("pending", "approved", "completed")))
    ).first()
    return existing is None


# ---------------------------------------------------------------------------
# Addresses CRUD
# ---------------------------------------------------------------------------
@account_bp.route("/addresses", methods=["GET"])
@requires_customer
def addresses_list() -> Response:
    customer = cast("Customer", g.current_customer)
    return make_response(
        render_template(
            "account/addresses.html",
            customer=customer,
            addresses=list(customer.addresses or []),
            error=None,
        )
    )


@account_bp.route("/addresses/new", methods=["POST"])
@requires_customer
def addresses_create() -> Response:
    from review_app.addresses import AddressInput, validate_and_persist

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    payload = AddressInput(
        name=(request.form.get("name") or "").strip() or None,
        line1=(request.form.get("line1") or "").strip(),
        line2=(request.form.get("line2") or "").strip() or None,
        city=(request.form.get("city") or "").strip(),
        state=(request.form.get("state") or "").strip(),
        zip_code=(request.form.get("zip") or "").strip(),
        country=(request.form.get("country") or "US").strip(),
        phone=(request.form.get("phone") or "").strip() or None,
        is_default=bool(request.form.get("is_default")),
    )
    if not (payload.line1 and payload.city and payload.state and payload.zip_code):
        flash("Address line, city, state, and ZIP are required.", "error")
        return cast(
            "Response", redirect(url_for("account.addresses_list"))
        )

    try:
        addr = validate_and_persist(s, customer.id, payload)
    except Exception as exc:
        flash(f"Could not validate address: {exc}", "error")
        return cast(
            "Response", redirect(url_for("account.addresses_list"))
        )

    if payload.is_default:
        for other in customer.addresses or []:
            if other.id != addr.id:
                other.is_default = False

    flash("Address saved.", "success")
    return cast(
        "Response", redirect(url_for("account.addresses_list"))
    )


@account_bp.route("/addresses/<uuid:address_id>/delete", methods=["POST"])
@requires_customer
def addresses_delete(address_id: uuid.UUID) -> Response:
    from review_app.addresses.models import Address

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    addr = s.get(Address, address_id)
    if addr is None or addr.customer_id != customer.id:
        flash("Address not found.", "error")
        return cast(
            "Response", redirect(url_for("account.addresses_list"))
        )
    s.delete(addr)
    flash("Address removed.", "success")
    return cast(
        "Response", redirect(url_for("account.addresses_list"))
    )


@account_bp.route("/addresses/<uuid:address_id>/default", methods=["POST"])
@requires_customer
def addresses_set_default(address_id: uuid.UUID) -> Response:
    from review_app.addresses.models import Address

    customer = cast("Customer", g.current_customer)
    s = _get_session()
    target = s.get(Address, address_id)
    if target is None or target.customer_id != customer.id:
        flash("Address not found.", "error")
        return cast(
            "Response", redirect(url_for("account.addresses_list"))
        )
    for a in customer.addresses or []:
        a.is_default = a.id == address_id
    flash("Default address updated.", "success")
    return cast(
        "Response", redirect(url_for("account.addresses_list"))
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@account_bp.route("/profile", methods=["GET", "POST"])
@requires_customer
def profile() -> Response:
    customer = cast("Customer", g.current_customer)
    s = _get_session()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip() or None
        email_raw = (request.form.get("email") or "").strip().lower()
        marketing = bool(request.form.get("marketing_opt_in"))

        customer.name = name
        customer.marketing_opt_in = marketing

        # Email change re-verifies via magic link.
        email_changed = email_raw and email_raw != customer.email
        if email_changed:
            if not _EMAIL_RE.match(email_raw):
                flash("That doesn't look like a valid email address.", "error")
                return make_response(
                    render_template("account/profile.html", customer=customer)
                )
            from review_app.account.auth import issue_token, send_magic_link

            secret_key = str(
                current_app.config.get("SECRET_KEY")
                or current_app.secret_key
                or ""
            )
            customer.email = email_raw
            s.flush()
            token = issue_token(
                s,
                customer_id=customer.id,
                secret_key=secret_key,
                ip_address=request.remote_addr,
            )
            send_magic_link(
                s,
                customer_email=customer.email,
                token=token,
                base_url=request.url_root,
            )
            session.pop("customer_id", None)
            flash(
                "Email updated. We sent a verification link to confirm — please sign in again.",
                "success",
            )
            return cast(
                "Response", redirect(url_for("account.login"))
            )

        flash("Profile updated.", "success")
        return cast(
            "Response", redirect(url_for("account.profile"))
        )

    return make_response(
        render_template("account/profile.html", customer=customer)
    )


__all__ = ["account_bp"]
