"""Append-only audit log (Phase 5a).

Two ways to record audit events:

1. **Explicit** — call :func:`record` from business code at the moment of
   the change, passing both ``before`` and ``after`` snapshots so the
   diff is auditable. Recommended for important actions
   (refunds, role changes, SKU updates).

2. **Auto-capture** — :func:`init_app` registers a Flask ``after_request``
   hook that records EVERY POST/PATCH/DELETE under ``/admin/*`` with a
   minimal payload (method, path, status). This is the safety net so
   nothing slips through, even when the developer forgets ``record``.

Routes can opt out of the auto-capture by adding ``audit_skip = True``
on the view function (use the :func:`skip` decorator).

The middleware NEVER raises. A DB error during write is logged, the
request continues. We tolerate occasional missed audit rows in exchange
for never breaking the user's flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask, Response
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


F = TypeVar("F", bound="Callable[..., Any]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def record(
    session: Session,
    *,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    user_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Insert one ``audit_log`` row inside the caller's transaction.

    ``user_id``, ``ip_address``, and ``user_agent`` default to the values
    on Flask-Login + ``request`` when called from a request context. Pass
    explicit values to record from a CLI/cron context.

    Does NOT commit. The caller's surrounding transaction is responsible.
    """
    from review_app.audit.models import AuditLogEntry

    if user_id is None:
        user_id = _current_user_id()
    if ip_address is None:
        ip_address = _current_ip()
    if user_agent is None:
        user_agent = _current_user_agent()

    entry = AuditLogEntry(
        user_id=_to_uuid(user_id),
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before=before,
        after=after,
        ip_address=ip_address,
        user_agent=(user_agent[:1024] if user_agent else None),
    )
    session.add(entry)


def skip(view: F) -> F:
    """Decorator: opt this Flask view out of the auto-capture middleware.

    Use on the audit log viewer itself + any read-only admin route that
    happens to be POST (rare).
    """
    view._audit_skip = True  # type: ignore[attr-defined]
    return view


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_user_id() -> str | None:
    try:
        from flask_login import current_user

        if not getattr(current_user, "is_authenticated", False):
            return None
        # User.get_id() returns a string (Flask-Login spec).
        uid = getattr(current_user, "get_id", lambda: None)()
        return str(uid) if uid else None
    except Exception:
        return None


def _current_ip() -> str | None:
    try:
        from flask import request

        # X-Forwarded-For first (we run behind Coolify's proxy).
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return request.remote_addr
    except Exception:
        return None


def _current_user_agent() -> str | None:
    try:
        from flask import request

        return request.headers.get("User-Agent")
    except Exception:
        return None


def _to_uuid(value: str | None) -> Any:
    """Coerce a user-id string to UUID; return None for invalid/missing."""
    if value is None:
        return None
    try:
        import uuid

        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Middleware (after_request hook)
# ---------------------------------------------------------------------------
_AUTO_CAPTURE_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "DELETE", "PUT"})
_initialized: bool = False


def init_app(app: Flask) -> None:
    """Register the after_request hook that auto-captures admin POST/PATCH/DELETE.

    Idempotent — safe to call multiple times across hot reloads.
    """
    global _initialized
    if getattr(app, "_wildprint_audit_attached", False):
        return

    @app.after_request
    def _audit_after_request(response: Response) -> Response:
        try:
            from flask import request

            # Only auto-capture admin state-changing requests.
            if request.method not in _AUTO_CAPTURE_METHODS:
                return response
            if not request.path.startswith("/admin"):
                return response

            # Honor @audit.skip on the matched view.
            view_func = app.view_functions.get(request.endpoint or "")
            if view_func is not None and getattr(view_func, "_audit_skip", False):
                return response

            # Don't audit failed responses — they didn't change state.
            if response.status_code >= 400:
                return response

            from review_app.db import get_session

            with get_session() as session:
                record(
                    session,
                    action=f"http.{request.method}",
                    target_type="admin_route",
                    target_id=request.path,
                    after={
                        "method": request.method,
                        "path": request.path,
                        "status": response.status_code,
                        "endpoint": request.endpoint or "",
                    },
                )
                # get_session() commits on clean exit.
        except Exception as exc:  # pragma: no cover - audit must NEVER raise
            logger.warning("audit.after_request failed: %s", exc)
        return response

    app._wildprint_audit_attached = True  # type: ignore[attr-defined]
    _initialized = True


__all__ = ["init_app", "record", "skip"]
