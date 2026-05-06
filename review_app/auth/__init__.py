"""Admin authentication package — Flask-Login + Argon2 + RBAC.

Public surface:
    - `login_manager`: the Flask-Login `LoginManager` singleton
    - `init_app(app)`: wire login_manager into a Flask app
    - `current_user`: re-export of Flask-Login's proxy
    - `requires_role`: role-gating decorator (no-op when ADMIN_AUTH_ENABLED is unset)

The wiring pass (Phase 0.12) calls `init_app(app)` from review_app/app.py.
Until then, this module is dormant — importing it has no side effects on the
existing app.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

from review_app.auth.decorators import requires_role
from review_app.auth.models import User

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

# Singleton instances. Created at import time but only attached to a Flask
# app inside `init_app(app)`.
login_manager: LoginManager = LoginManager()
login_manager.login_view = "auth.login"  # endpoint, not URL — Blueprint-prefixed
login_manager.session_protection = "strong"

# Use a separate session cookie key for admin auth so we never collide with
# the existing `unlocked` cookie used by the $49 unlock flow.
login_manager.login_message_category = "info"

csrf: CSRFProtect = CSRFProtect()


@login_manager.user_loader  # type: ignore[untyped-decorator]  # flask-login user_loader is loosely typed
def _load_user(user_id: str) -> User | None:
    """Flask-Login user loader. Returns None for unknown / soft-deleted users."""
    from review_app.db import get_session

    with get_session() as session:
        return User.get_active_by_id(session, user_id)


def init_app(app: Flask) -> None:
    """Attach login_manager + CSRF to the given Flask app.

    Idempotent: safe to call multiple times. Does NOT register the auth
    blueprint — that's the wiring pass's responsibility (so it can choose the
    URL prefix). Does NOT enforce auth — `requires_role` honors the
    ADMIN_AUTH_ENABLED env flag and stays a passthrough until cutover.
    """
    login_manager.init_app(app)
    csrf.init_app(app)

    if not _admin_auth_enabled():
        # Loud one-time warning so it's obvious in logs that protected
        # routes are still wide-open. This is intentional during the
        # shadow-mode rollout (see docs/auth-bootstrap.md).
        app.logger.warning(
            "ADMIN_AUTH_ENABLED is not set — @requires_role is a no-op. "
            "Admin routes remain unauthenticated. Flip ADMIN_AUTH_ENABLED=true "
            "in Coolify after bootstrapping the first admin user."
        )


def _admin_auth_enabled() -> bool:
    """Single source of truth for the opt-in flag. Mirrors decorators.py."""
    return os.environ.get("ADMIN_AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "User",
    "csrf",
    "current_user",
    "init_app",
    "login_manager",
    "requires_role",
]
