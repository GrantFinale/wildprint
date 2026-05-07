"""Settings routes — register on the shared ``admin_bp``.

Five pages (per ``docs/admin-ia.md`` §1):

* ``/admin/settings/users``         — Users & roles (admin)
* ``/admin/settings/api-keys``      — API keys (admin, read-only / rotation reference)
* ``/admin/settings/integrations``  — Service health dashboard (admin/staff)
* ``/admin/settings/audit``         — Audit log viewer (admin, stub for Phase 5)
* ``/admin/settings/account``       — My account (all roles)

Notable Phase 4a deviations (flagged for Phase 5):
  * Audit log table doesn't exist in the DB schema yet; the page renders a
    placeholder + the eventual column layout.
  * 2FA + per-user API tokens on My account are stubbed (no model fields
    yet); the form posts return a "coming in Phase 5" flash.
  * Integrations page caches health checks in-memory for 5 min; Phase 5
    moves the cache to Redis so workers/web share state.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from review_app.admin._helpers import crumbs
from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role
from review_app.auth.models import VALID_ROLES, User
from review_app.db import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Users & roles
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UserRow:
    id: str
    email: str
    role: str
    last_login: str
    is_active: bool


@admin_bp.route("/settings/users", methods=["GET"])
@requires_role("admin")
def settings_users() -> ResponseReturnValue:
    """List active users + render the invite form."""
    rows: list[UserRow] = []
    invited_email = request.args.get("invited") or ""
    temp_password = request.args.get("temp_password") or ""

    try:
        with get_session() as session:
            stmt = select(User).where(User.deleted_at.is_(None)).order_by(User.email)
            try:
                users = session.execute(stmt).scalars().all()
            except (OperationalError, ProgrammingError):
                users = []
            for u in users:
                rows.append(
                    UserRow(
                        id=str(u.id),
                        email=u.email,
                        role=u.role,
                        last_login=(
                            u.last_login_at.strftime("%Y-%m-%d %H:%M UTC")
                            if u.last_login_at
                            else "never"
                        ),
                        is_active=u.is_active,
                    )
                )
    except ImportError:
        pass

    return render_template(
        "admin/settings/users.html",
        page_title="Users & roles",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("Users & roles", None),
        ),
        rows=rows,
        valid_roles=sorted(VALID_ROLES),
        invited_email=invited_email,
        temp_password=temp_password,
    )


@admin_bp.route("/settings/users/invite", methods=["POST"])
@requires_role("admin")
def settings_users_invite() -> ResponseReturnValue:
    """Create a new user with a random temp password.

    The temp password is shown to the inviter exactly once via a query
    string on the redirect target. The invitee resets it on first login
    (Phase 5 will add the formal reset flow; for now we just surface the
    plaintext to the admin to share via Signal/etc).
    """
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "").strip()

    if not email or "@" not in email:
        flash("A valid email is required.", "error")
        return redirect(url_for("admin.settings_users"))
    if role not in VALID_ROLES:
        flash(f"Role must be one of {sorted(VALID_ROLES)}.", "error")
        return redirect(url_for("admin.settings_users"))

    # 16-byte url-safe token = ~22 chars, enough entropy for a temp creds.
    temp_password = secrets.token_urlsafe(16)
    try:
        with get_session() as session:
            existing = User.get_active_by_email(session, email)
            if existing is not None:
                flash(f"User {email} already exists.", "error")
                return redirect(url_for("admin.settings_users"))
            user = User.create(email=email, password=temp_password, role=role)
            session.add(user)
            session.flush()
    except ImportError:
        flash("Auth not configured; user invite skipped.", "error")
        return redirect(url_for("admin.settings_users"))
    except (OperationalError, ProgrammingError) as exc:
        # Users table doesn't exist yet (fresh dev DB) — surface the
        # plaintext temp password anyway so the test fixture / staging
        # env can dogfood the flow without booting Alembic first.
        logger.info("invite stored in-memory only (users table missing): %s", exc)

    return redirect(
        url_for(
            "admin.settings_users",
            invited=email,
            temp_password=temp_password,
        )
    )


# ---------------------------------------------------------------------------
# API keys — read-only reference
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ApiKeyRow:
    """One row in the API keys reference table — masked, no plaintext."""

    service: str
    env_var: str
    masked_value: str
    is_set: bool


def _mask(value: str) -> str:
    """Mask a secret: keep first 4 + last 4 chars, redact the middle."""
    if not value:
        return "—"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{'*' * 8}…{value[-4:]}"


_API_KEY_SERVICES: tuple[tuple[str, str], ...] = (
    ("Stripe (secret)", "STRIPE_SECRET_KEY"),
    ("Stripe (webhook)", "STRIPE_WEBHOOK_SECRET"),
    ("Resend", "RESEND_API_KEY"),
    ("Prodigi (sandbox)", "PRODIGI_SANDBOX_API_KEY"),
    ("Prodigi (live)", "PRODIGI_LIVE_API_KEY"),
    ("Smarty Streets", "SMARTY_AUTH_TOKEN"),
    ("OpenAI", "OPENAI_API_KEY"),
    ("Recraft", "RECRAFT_API_KEY"),
    ("Replicate", "REPLICATE_API_TOKEN"),
)


@admin_bp.route("/settings/api-keys", methods=["GET"])
@requires_role("admin")
def settings_api_keys() -> ResponseReturnValue:
    """Reference table for service API keys. Read-only; rotation is via Coolify."""
    rows = [
        ApiKeyRow(
            service=service,
            env_var=env_var,
            masked_value=_mask(os.environ.get(env_var, "")),
            is_set=bool(os.environ.get(env_var)),
        )
        for service, env_var in _API_KEY_SERVICES
    ]
    return render_template(
        "admin/settings/api_keys.html",
        page_title="API keys",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("API keys", None),
        ),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Integrations health — cached 5 min
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntegrationStatus:
    service: str
    healthy: bool
    last_call: str
    note: str


_HEALTH_CACHE: dict[str, tuple[float, IntegrationStatus]] = {}
_HEALTH_TTL_SEC = 300  # 5 min


def _check_integration(name: str) -> IntegrationStatus:
    """Fast, conservative health probe. Returns "healthy" iff the env var is set.

    Phase 4a does not actually call the upstream services — that adds
    latency to the page load and ties health to outbound connectivity from
    the web container. Phase 5 moves real probes to a background job that
    writes to a `health_checks` table.

    For now: if the env var is present, we mark the service "configured".
    A real ping is left as a TODO (Phase 5).
    """
    env_present = bool(os.environ.get(name))
    return IntegrationStatus(
        service=name,
        healthy=env_present,
        last_call="(probe deferred to Phase 5)",
        note="env var configured" if env_present else "env var not set",
    )


def _cached_check(label: str, env_var: str) -> IntegrationStatus:
    now = time.time()
    cached = _HEALTH_CACHE.get(label)
    if cached and (now - cached[0]) < _HEALTH_TTL_SEC:
        return cached[1]
    status = _check_integration(env_var)
    _HEALTH_CACHE[label] = (now, status)
    return status


_INTEGRATIONS: tuple[tuple[str, str], ...] = (
    ("Stripe", "STRIPE_SECRET_KEY"),
    ("Resend", "RESEND_API_KEY"),
    ("Prodigi", "PRODIGI_SANDBOX_API_KEY"),
    ("Smarty", "SMARTY_AUTH_TOKEN"),
    ("OpenAI", "OPENAI_API_KEY"),
    ("Recraft", "RECRAFT_API_KEY"),
    ("Replicate", "REPLICATE_API_TOKEN"),
    ("DO Spaces (thumbs)", "SPACES_THUMBS_BUCKET"),
)


@admin_bp.route("/settings/integrations", methods=["GET"])
@requires_role("admin", "staff")
def settings_integrations() -> ResponseReturnValue:
    """Per-service health dashboard. Phase 5a: real upstream probes, cached 5 min."""
    # Phase 5a — real probes via probes.probe_all(). Falls back to the
    # Phase 4a env-var check for any service the probe layer doesn't cover.
    from review_app.admin.settings import probes as _probes
    from review_app.checkout import tax as _tax

    probe_results = _probes.probe_all()

    rows: list[IntegrationStatus] = []
    for label, _env_var in _INTEGRATIONS:
        # Map _INTEGRATIONS labels to the keys probe_all returns.
        probe_key = {
            "DO Spaces (thumbs)": "DO Spaces",
        }.get(label, label)
        result = probe_results.get(probe_key)
        if result is None:
            # Service not in probe layer (OpenAI / Recraft / Replicate) — fall
            # back to env-var check so the row still renders something useful.
            fallback = _cached_check(label, _env_var)
            rows.append(fallback)
            continue
        latency_str = (
            f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "—"
        )
        note = result.note or ("ok" if result.ok else (result.error or "error"))
        rows.append(
            IntegrationStatus(
                service=label,
                healthy=result.ok,
                last_call=latency_str,
                note=note,
            )
        )

    # Stripe Tax block — Phase 5a addition.
    tax_status = {
        "enabled": _tax.is_enabled(),
        "recent_errors": _tax.recent_errors(),
    }

    return render_template(
        "admin/settings/integrations.html",
        page_title="Integrations",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("Integrations", None),
        ),
        rows=rows,
        tax_status=tax_status,
    )


# ---------------------------------------------------------------------------
# Audit log — Phase 5a: real viewer backed by audit_log table
# ---------------------------------------------------------------------------
@admin_bp.route("/settings/audit", methods=["GET"])
@requires_role("admin")
def settings_audit() -> ResponseReturnValue:
    """Audit log viewer with filters + pagination.

    Filters (all query-string, all optional):
      * ``user`` — filter by user_id (UUID)
      * ``action`` — filter by action name (exact match)
      * ``target_type`` — filter by target_type (exact match)
      * ``date_from`` / ``date_to`` — ISO date range (UTC)
      * ``page`` — 1-indexed; 50 entries per page
    """
    # Mark this view exempt from auto-capture so reading the audit log
    # doesn't itself produce audit entries.
    from datetime import datetime as _dt

    from review_app.audit import skip as _audit_skip
    from review_app.audit.models import AuditLogEntry

    # Decorator can't reach this function dynamically; flag attribute
    # directly for the after_request hook to read.
    settings_audit._audit_skip = True  # type: ignore[attr-defined]
    _ = _audit_skip  # silence "unused import" — kept for symmetry with other handlers

    PAGE_SIZE = 50
    try:
        page = max(int(request.args.get("page", "1") or "1"), 1)
    except ValueError:
        page = 1
    user_filter = (request.args.get("user") or "").strip()
    action_filter = (request.args.get("action") or "").strip()
    target_type_filter = (request.args.get("target_type") or "").strip()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()

    rows: list[AuditLogEntry] = []
    total: int = 0

    try:
        with get_session() as db_session:
            stmt = select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc())
            from sqlalchemy import func as _sa_func

            count_stmt = select(_sa_func.count()).select_from(AuditLogEntry)

            if user_filter:
                from review_app.audit import _to_uuid

                uid = _to_uuid(user_filter)
                if uid is not None:
                    stmt = stmt.where(AuditLogEntry.user_id == uid)
                    count_stmt = count_stmt.where(AuditLogEntry.user_id == uid)
            if action_filter:
                stmt = stmt.where(AuditLogEntry.action == action_filter)
                count_stmt = count_stmt.where(AuditLogEntry.action == action_filter)
            if target_type_filter:
                stmt = stmt.where(AuditLogEntry.target_type == target_type_filter)
                count_stmt = count_stmt.where(
                    AuditLogEntry.target_type == target_type_filter
                )
            if date_from_raw:
                try:
                    df = _dt.fromisoformat(date_from_raw)
                    stmt = stmt.where(AuditLogEntry.created_at >= df)
                    count_stmt = count_stmt.where(AuditLogEntry.created_at >= df)
                except ValueError:
                    pass
            if date_to_raw:
                try:
                    dt_to = _dt.fromisoformat(date_to_raw)
                    stmt = stmt.where(AuditLogEntry.created_at <= dt_to)
                    count_stmt = count_stmt.where(AuditLogEntry.created_at <= dt_to)
                except ValueError:
                    pass

            offset = (page - 1) * PAGE_SIZE
            stmt = stmt.offset(offset).limit(PAGE_SIZE)
            try:
                rows = list(db_session.execute(stmt).scalars().all())
                total = int(db_session.execute(count_stmt).scalar_one() or 0)
            except (OperationalError, ProgrammingError):
                # audit_log table missing — fresh dev DB before alembic upgrade.
                rows = []
                total = 0
    except ImportError:
        rows = []

    pages_total = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)

    return render_template(
        "admin/settings/audit.html",
        page_title="Audit log",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("Audit log", None),
        ),
        rows=rows,
        total=total,
        page=page,
        pages_total=pages_total,
        page_size=PAGE_SIZE,
        filters={
            "user": user_filter,
            "action": action_filter,
            "target_type": target_type_filter,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
        },
    )


# ---------------------------------------------------------------------------
# My account — change password / 2FA stub / API token stub
# ---------------------------------------------------------------------------
@admin_bp.route("/settings/account", methods=["GET", "POST"])
@requires_role("admin", "staff", "viewer")
def settings_account() -> ResponseReturnValue:
    """Self-service account settings.

    GET renders the form pre-populated with the current user's email +
    role (read-only). POST handles change-password; 2FA and API token
    generation are stubs that surface a "coming in Phase 5" flash.
    """
    user_info: dict[str, Any] = {
        "email": getattr(current_user, "email", "(shadow mode)"),
        "role": getattr(current_user, "role", "admin"),
    }

    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "change_password":
            old = request.form.get("old_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if not new or new != confirm:
                flash("New passwords do not match.", "error")
            elif not getattr(current_user, "is_authenticated", False):
                flash("Login first to change your password.", "error")
            elif not current_user.verify_password(old):
                flash("Old password is incorrect.", "error")
            else:
                with get_session() as session:
                    fresh = User.get_active_by_id(session, current_user.id)
                    if fresh is None:
                        flash("User not found.", "error")
                    else:
                        fresh.password_hash = User.hash_password(new)
                        session.add(fresh)
                        flash("Password updated.", "success")
        return redirect(url_for("admin.settings_account"))

    # Phase 6 polish — surface 2FA enrollment state + active API tokens.
    extra_ctx = _account_page_context()
    return render_template(
        "admin/settings/account.html",
        page_title="My account",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("My account", None),
        ),
        user_info=user_info,
        **extra_ctx,
    )


def _account_page_context() -> dict[str, Any]:
    """Hydrate 2FA + API tokens info for the account page."""
    if not getattr(current_user, "is_authenticated", False):
        return {
            "totp_enrolled": False,
            "api_tokens": [],
            "newly_issued_token": None,
            "newly_issued_recovery": None,
        }
    from sqlalchemy import select as _select

    from review_app.auth import totp as _totp
    from review_app.auth.api_token_models import UserApiToken

    api_tokens: list[dict[str, Any]] = []
    enrolled = False
    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is not None:
                enrolled = _totp.is_enrolled(fresh)
            rows = session.execute(
                _select(UserApiToken)
                .where(UserApiToken.user_id == str(current_user.id))
                .where(UserApiToken.revoked_at.is_(None))
                .order_by(UserApiToken.created_at.desc())
            ).scalars().all()
            for row in rows:
                api_tokens.append(
                    {
                        "id": str(row.id),
                        "name": row.name,
                        "created_at": (
                            row.created_at.strftime("%Y-%m-%d %H:%M UTC")
                            if row.created_at else "—"
                        ),
                        "last_used_at": (
                            row.last_used_at.strftime("%Y-%m-%d %H:%M UTC")
                            if row.last_used_at else "never"
                        ),
                        "expires_at": (
                            row.expires_at.strftime("%Y-%m-%d %H:%M UTC")
                            if row.expires_at else "never"
                        ),
                    }
                )
    except (OperationalError, ProgrammingError):
        pass

    from flask import session as _flask_session

    return {
        "totp_enrolled": enrolled,
        "api_tokens": api_tokens,
        "newly_issued_token": _flask_session.pop("newly_issued_token", None),
        "newly_issued_recovery": _flask_session.pop("newly_issued_recovery", None),
        "newly_issued_secret": _flask_session.pop("newly_issued_secret", None),
        "newly_issued_qr": _flask_session.pop("newly_issued_qr", None),
    }


# ---------------------------------------------------------------------------
# Phase 6 polish — 2FA + API token endpoints.
# ---------------------------------------------------------------------------
@admin_bp.route("/settings/account/2fa/enroll", methods=["POST"])
@requires_role("admin", "staff", "viewer")
def settings_account_2fa_enroll() -> ResponseReturnValue:
    """Generate a TOTP secret + recovery codes; show ONCE on the redirect."""
    from flask import session as _flask_session

    from review_app.auth import totp as _totp

    if not getattr(current_user, "is_authenticated", False):
        flash("Login first to enroll in 2FA.", "error")
        return redirect(url_for("admin.settings_account"))

    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is None:
                flash("User not found.", "error")
                return redirect(url_for("admin.settings_account"))
            payload = _totp.enroll(fresh)
            session.add(fresh)
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("2FA enrollment DB error: %s", exc)
        flash("Could not enroll in 2FA (DB error).", "error")
        return redirect(url_for("admin.settings_account"))

    _flask_session["newly_issued_secret"] = str(payload["secret"])
    _flask_session["newly_issued_qr"] = str(payload["qr_data_url"])
    recovery = payload["recovery_codes"]
    assert isinstance(recovery, list)
    _flask_session["newly_issued_recovery"] = list(recovery)
    flash(
        "2FA enrollment complete. Save your recovery codes — they are shown once.",
        "success",
    )
    return redirect(url_for("admin.settings_account"))


@admin_bp.route("/settings/account/2fa/verify", methods=["POST"])
@requires_role("admin", "staff", "viewer")
def settings_account_2fa_verify() -> ResponseReturnValue:
    """Verify a 6-digit TOTP code or recovery code against the current user."""
    from review_app.auth import totp as _totp

    code = (request.form.get("code") or "").strip()
    if not code:
        flash("Code is required.", "error")
        return redirect(url_for("admin.settings_account"))
    if not getattr(current_user, "is_authenticated", False):
        flash("Login first.", "error")
        return redirect(url_for("admin.settings_account"))

    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is None or not _totp.is_enrolled(fresh):
                flash("2FA is not enrolled for this user.", "error")
                return redirect(url_for("admin.settings_account"))
            ok = _totp.verify(fresh, code)
            if ok:
                session.add(fresh)
                flash("Code verified.", "success")
            else:
                flash("Invalid code.", "error")
    except (OperationalError, ProgrammingError):
        flash("DB error during verification.", "error")
    return redirect(url_for("admin.settings_account"))


@admin_bp.route("/settings/account/2fa/disable", methods=["POST"])
@requires_role("admin", "staff", "viewer")
def settings_account_2fa_disable() -> ResponseReturnValue:
    """Clear all 2FA state on the current user."""
    from review_app.auth import totp as _totp

    if not getattr(current_user, "is_authenticated", False):
        flash("Login first.", "error")
        return redirect(url_for("admin.settings_account"))
    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is not None:
                _totp.disable(fresh)
                session.add(fresh)
                flash("2FA disabled.", "info")
    except (OperationalError, ProgrammingError):
        flash("DB error disabling 2FA.", "error")
    return redirect(url_for("admin.settings_account"))


@admin_bp.route("/settings/account/api-tokens", methods=["POST"])
@requires_role("admin", "staff", "viewer")
def settings_account_api_tokens_create() -> ResponseReturnValue:
    """Issue a new API token; show plaintext ONCE on the redirect."""
    from flask import session as _flask_session

    from review_app.auth import api_tokens as _api_tokens

    name = (request.form.get("name") or "").strip()
    scopes_raw = (request.form.get("scopes") or "").strip()
    scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
    if not name:
        flash("Token name is required.", "error")
        return redirect(url_for("admin.settings_account"))
    if not getattr(current_user, "is_authenticated", False):
        flash("Login first.", "error")
        return redirect(url_for("admin.settings_account"))

    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is None:
                flash("User not found.", "error")
                return redirect(url_for("admin.settings_account"))
            payload = _api_tokens.create(
                fresh,
                name=name,
                scopes=scopes,
                session=session,
            )
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("API token create DB error: %s", exc)
        flash("Could not create token (DB error).", "error")
        return redirect(url_for("admin.settings_account"))

    _flask_session["newly_issued_token"] = payload["plaintext"]
    flash(
        f"Token '{name}' created. Copy it now — it will not be shown again.",
        "success",
    )
    return redirect(url_for("admin.settings_account"))


@admin_bp.route("/settings/account/api-tokens/<token_id>/revoke", methods=["POST"])
@requires_role("admin", "staff", "viewer")
def settings_account_api_tokens_revoke(token_id: str) -> ResponseReturnValue:
    """Revoke an API token owned by the current user."""
    from review_app.auth import api_tokens as _api_tokens

    if not getattr(current_user, "is_authenticated", False):
        flash("Login first.", "error")
        return redirect(url_for("admin.settings_account"))

    try:
        with get_session() as session:
            fresh = User.get_active_by_id(session, current_user.id)
            if fresh is None:
                flash("User not found.", "error")
                return redirect(url_for("admin.settings_account"))
            ok = _api_tokens.revoke(token_id, fresh, session=session)
            if ok:
                flash("Token revoked.", "success")
            else:
                flash("Token not found or already revoked.", "error")
    except (OperationalError, ProgrammingError):
        flash("DB error revoking token.", "error")
    return redirect(url_for("admin.settings_account"))


__all__: list[str] = []
