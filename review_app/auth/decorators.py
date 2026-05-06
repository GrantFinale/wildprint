"""Role-gating decorator. Honors the ADMIN_AUTH_ENABLED feature flag.

When `ADMIN_AUTH_ENABLED` is unset (or falsy), the decorator is a complete
passthrough — this lets us ship auth code into prod without locking ourselves
out before the bootstrap admin user exists. The cutover (Phase 0.12) flips
the env var to `true` in Coolify.

When enabled:
    - Anonymous user            -> 401 (with a redirect hint to /admin/login)
    - Authenticated wrong-role  -> 403
    - Authenticated right-role  -> handler runs
"""
from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable, TypeVar

from flask import Response, current_app, redirect, request, url_for
from flask_login import current_user

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Module-level latch so the "shadow mode" warning fires exactly once per
# process, on first decorator invocation (not at import time — env vars may
# be set after this module imports).
_shadow_warned: bool = False


def _admin_auth_enabled() -> bool:
    return os.environ.get("ADMIN_AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _warn_shadow_once() -> None:
    global _shadow_warned
    if _shadow_warned:
        return
    _shadow_warned = True
    msg = (
        "@requires_role is in SHADOW MODE — ADMIN_AUTH_ENABLED is unset. "
        "All role-gated routes are accessible without authentication. "
        "This is intentional during Phase 0.6 rollout; flip "
        "ADMIN_AUTH_ENABLED=true after creating the bootstrap admin user."
    )
    # Prefer Flask app logger if available, fall back to module logger.
    try:
        current_app.logger.warning(msg)
    except RuntimeError:
        logger.warning(msg)


def requires_role(*roles: str) -> Callable[[F], F]:
    """Gate a Flask view on Flask-Login auth + role membership.

    Usage::

        @app.route("/admin/dashboard")
        @requires_role("admin", "staff")
        def dashboard():
            ...

    Returns 401 (with a Location header to /admin/login) for anon users when
    the request accepts HTML; otherwise a JSON 401. Returns 403 for the
    wrong-role case in either content type.
    """
    if not roles:
        raise ValueError("@requires_role requires at least one role argument.")

    def decorator(view: F) -> F:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _admin_auth_enabled():
                _warn_shadow_once()
                return view(*args, **kwargs)

            # Anonymous: 401 + redirect for browsers, JSON for APIs.
            if not current_user.is_authenticated:
                if request.accept_mimetypes.best == "application/json" or request.is_json:
                    return Response(
                        '{"error": "authentication required"}',
                        status=401,
                        mimetype="application/json",
                    )
                # Browser: bounce to login with a `next` param so the user
                # lands back on the originally requested page.
                login_url = url_for("auth.login", next=request.url)
                resp = redirect(login_url)
                resp.status_code = 401
                return resp

            # Authenticated but wrong role.
            user_role = getattr(current_user, "role", None)
            if user_role not in roles:
                return Response(
                    f"forbidden: requires one of {sorted(roles)}",
                    status=403,
                    mimetype="text/plain",
                )

            return view(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["requires_role"]
