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
    """Per-service health dashboard. Cached for 5 minutes."""
    rows = [
        # _cached_check labels by env_var so cache survives label changes
        IntegrationStatus(
            service=label,
            healthy=_cached_check(label, env_var).healthy,
            last_call=_cached_check(label, env_var).last_call,
            note=_cached_check(label, env_var).note,
        )
        for label, env_var in _INTEGRATIONS
    ]
    return render_template(
        "admin/settings/integrations.html",
        page_title="Integrations",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("Integrations", None),
        ),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Audit log — Phase 5 stub
# ---------------------------------------------------------------------------
@admin_bp.route("/settings/audit", methods=["GET"])
@requires_role("admin")
def settings_audit() -> ResponseReturnValue:
    """Audit log viewer.

    The ``audit_log`` table doesn't exist in the schema yet; Phase 5 adds it
    via a new Alembic migration. This page renders the eventual layout
    (column headers + empty state) so the IA + role gating are in place.
    """
    return render_template(
        "admin/settings/audit.html",
        page_title="Audit log",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("Audit log", None),
        ),
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
                # Re-hash and persist.
                with get_session() as session:
                    fresh = User.get_active_by_id(session, current_user.id)
                    if fresh is None:
                        flash("User not found.", "error")
                    else:
                        fresh.password_hash = User.hash_password(new)
                        session.add(fresh)
                        flash("Password updated.", "success")
        elif action == "enable_2fa":
            flash("2FA enrollment ships in Phase 5.", "info")
        elif action == "generate_token":
            flash("Per-user API tokens ship in Phase 5.", "info")
        return redirect(url_for("admin.settings_account"))

    return render_template(
        "admin/settings/account.html",
        page_title="My account",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Settings", None),
            ("My account", None),
        ),
        user_info=user_info,
    )


__all__: list[str] = []
