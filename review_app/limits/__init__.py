"""Flask-Limiter rate limiting (Phase 5a).

Provides:

* :data:`limiter` — process-wide :class:`flask_limiter.Limiter`. Routes
  decorate themselves with ``@limiter.limit(...)`` / ``@limiter.exempt``.
* :func:`init_app` — wire the limiter to a Flask app. Idempotent.
* :func:`render_limit`, :func:`cart_limit`, :func:`global_limit` —
  pre-built decorators that read the current per-env limit values, so
  bumping a limit only requires an env var change + a pod restart.
* :func:`is_admin_request`, :func:`is_webhook_request` — convenience
  helpers for routes that want to short-circuit the limiter.

Storage backend
---------------
Reads ``REDIS_URL`` to share rate counters across web replicas. When
``REDIS_URL`` is unset (local dev / unit tests), falls back to
in-memory storage with a one-time warning logged.

Identity
--------
Anonymous traffic is keyed by IP (X-Forwarded-For first, falling back to
``request.remote_addr``). Authenticated traffic prepends the user id so a
single IP can host many users (e.g. an office) without sharing one bucket.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — overridable via env vars
# ---------------------------------------------------------------------------
def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("limits: invalid %s=%r, falling back to %d", name, raw, default)
        return default


def _render_per_min() -> int:
    return _int_env("RATE_LIMIT_RENDER_PER_MIN", 5)


def _cart_per_min() -> int:
    return _int_env("RATE_LIMIT_CART_PER_MIN", 30)


def _global_per_min() -> int:
    return _int_env("RATE_LIMIT_GLOBAL_PER_MIN", 120)


# Public string-form limits read by the limiter at decoration time.
def render_limit_string() -> str:
    return f"{_render_per_min()}/minute"


def cart_limit_string() -> str:
    return f"{_cart_per_min()}/minute"


def global_limit_string() -> str:
    return f"{_global_per_min()}/minute"


# ---------------------------------------------------------------------------
# Identity function
# ---------------------------------------------------------------------------
def _identity_key() -> str:
    """Return the rate-limit bucket key for the current request.

    Format:
      - ``ip:<addr>`` for anonymous requests
      - ``user:<uuid>+ip:<addr>`` for authenticated requests
    """
    ip = get_remote_address() or "unknown"
    try:
        # Late import — flask-login is wired only in production-style apps.
        from flask_login import current_user

        if current_user.is_authenticated:
            user_id = getattr(current_user, "id", None) or getattr(
                current_user, "get_id", lambda: ""
            )()
            return f"user:{user_id}+ip:{ip}"
    except Exception:
        # No flask-login wired, or no app context — fall through to IP-only.
        pass
    return f"ip:{ip}"


# ---------------------------------------------------------------------------
# Limiter instance
# ---------------------------------------------------------------------------
def _storage_uri() -> str:
    """Pick the rate-counter backend.

    Prefers ``REDIS_URL`` (shared across replicas). Falls back to
    in-memory which is fine for tests / single-process dev.
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        return redis_url
    return "memory://"


_default_limits_factory: Final[Callable[[], list[str]]] = lambda: [global_limit_string()]


limiter = Limiter(
    key_func=_identity_key,
    default_limits=[],  # populated in init_app — needs env evaluation at app boot
    storage_uri=_storage_uri(),
    strategy="fixed-window",  # cheapest, ample for our scale; sliding-window if abuse becomes real
    headers_enabled=True,
)


# ---------------------------------------------------------------------------
# Pre-built decorators
# ---------------------------------------------------------------------------
def render_limit() -> "Callable[..., Any]":
    """Decorator: apply the render limit to a view."""
    return limiter.limit(render_limit_string)


def cart_limit() -> "Callable[..., Any]":
    """Decorator: apply the cart limit to a view."""
    return limiter.limit(cart_limit_string)


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------
def is_admin_request() -> bool:
    """True when the request URL starts with ``/admin``.

    Admin routes are gated by Flask-Login + role check + (in prod) a
    Coolify-level IP allowlist, so they're already protected from abuse.
    Layering Flask-Limiter on top would just punish legitimate bursts of
    catalog edits.
    """
    try:
        from flask import request

        return request.path.startswith("/admin")
    except Exception:
        return False


def is_webhook_request() -> bool:
    """True when the request URL is a verified webhook endpoint.

    Webhooks (``/api/stripe/webhook`` and ``/api/prodigi/webhook``) are
    authenticated by signature + idempotency, so a remote attacker can't
    trigger them at scale. Rate limiting them would risk dropping a
    legitimate burst from Stripe / Prodigi.
    """
    try:
        from flask import request

        path = request.path
        if path.startswith("/api/stripe/webhook"):
            return True
        if path.startswith("/api/prodigi/webhook"):
            return True
        if path.startswith("/api/webhook"):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# init_app
# ---------------------------------------------------------------------------
_initialized: bool = False


def init_app(app: "Flask") -> None:
    """Wire the limiter into ``app``. Idempotent."""
    global _initialized
    if _initialized and getattr(app, "_wildprint_limiter_attached", False):
        return

    # Apply default global limit at boot time (env vars are now stable).
    limiter.default_limits = [global_limit_string()]
    limiter.init_app(app)

    # Auto-exempt admin + webhook routes via the request_filter API.
    @limiter.request_filter
    def _exempt_admin_and_webhooks() -> bool:
        return is_admin_request() or is_webhook_request()

    setattr(app, "_wildprint_limiter_attached", True)
    _initialized = True
    logger.info(
        "limits: initialised storage=%s render=%s cart=%s global=%s",
        _storage_uri(),
        render_limit_string(),
        cart_limit_string(),
        global_limit_string(),
    )


__all__ = [
    "cart_limit",
    "cart_limit_string",
    "global_limit_string",
    "init_app",
    "is_admin_request",
    "is_webhook_request",
    "limiter",
    "render_limit",
    "render_limit_string",
]
