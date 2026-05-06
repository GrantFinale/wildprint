"""structlog configuration + Flask request-context binding.

Used by *new* code paths only. Legacy `print()` and `app.logger.*` calls
in `review_app/app.py` are intentionally untouched (Phase 0.9 acceptance
criterion).

Usage::

    from review_app.observability import get_logger
    log = get_logger(__name__)
    log.info("rendered_poster", species_count=12, layout="editorial_3x3")
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import structlog
from structlog.types import Processor

# Module-level guard so we only configure structlog once per process even
# if `init_app` is called from multiple entry points (web + worker).
_CONFIGURED: bool = False


def _is_dev() -> bool:
    """Return True when the app is running in a developer-friendly env.

    We treat anything that isn't explicitly `production` or `staging` as
    dev so local `flask run`, pytest, and ad-hoc scripts all get the
    pretty console renderer.
    """
    env = os.getenv("FLASK_ENV", "development").lower()
    return env not in ("production", "staging", "prod")


def _resolve_log_level() -> int:
    """Read `LOG_LEVEL` from env (default INFO) and translate to logging int."""
    raw = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, raw, logging.INFO)


def configure_structlog() -> None:
    """Idempotently configure structlog + the stdlib root logger.

    Uses a JSON renderer in non-dev environments and the colourised
    console renderer in dev. Adds standard processors: timestamper, log
    level, logger name, callsite (filename + lineno), and exception
    formatting.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = _resolve_log_level()
    logging.basicConfig(format="%(message)s", level=level)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
    ]

    renderer: Processor
    if _is_dev():
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str | None = None) -> Any:
    """Return a structlog bound logger.

    Safe to call before `configure_structlog()` — structlog will lazily
    pick up the configuration on first use, but callers in app code
    should rely on `init_app(app)` having run during boot.
    """
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_request_context(**fields: Any) -> None:
    """Bind arbitrary fields to the current request's structlog context.

    Thin wrapper around ``structlog.contextvars.bind_contextvars`` so
    callers don't need to import the contextvars module directly.
    """
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """Clear all contextvars bound during the current request."""
    structlog.contextvars.clear_contextvars()


def install_flask_request_hooks(app: Any) -> None:
    """Attach `before_request` / `teardown_request` hooks to a Flask app.

    On each request:
    - generate a UUID4 `request_id`
    - bind `request_id`, the request method+path, and (if Flask-Login is
      installed and a user is authenticated) `user_id` into the structlog
      contextvars so every log line emitted during the request includes
      them automatically
    - clear the context on teardown so RQ workers / next requests start
      clean
    """
    from flask import (  # local import: keep module importable without Flask at import time
        g,
        request,
    )

    @app.before_request  # type: ignore[untyped-decorator]
    def _bind_obs_context() -> None:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        g.request_id = request_id
        ctx: dict[str, Any] = {
            "request_id": request_id,
            "http_method": request.method,
            "http_path": request.path,
        }
        user_id = _try_current_user_id()
        if user_id is not None:
            ctx["user_id"] = user_id
        bind_request_context(**ctx)

    @app.teardown_request  # type: ignore[untyped-decorator]
    def _clear_obs_context(_exc: BaseException | None) -> None:
        clear_request_context()


def _try_current_user_id() -> str | int | None:
    """Best-effort lookup of the logged-in user's id via Flask-Login.

    Returns None if Flask-Login isn't installed, no user is logged in,
    or anything goes wrong — observability must never crash the request.
    """
    try:
        from flask_login import current_user
    except Exception:
        return None
    try:
        if getattr(current_user, "is_authenticated", False):
            uid = getattr(current_user, "id", None)
            if uid is not None:
                # getattr() returns Any; the runtime type is whatever Flask-Login
                # sets `id` to (typically str), but mypy can't narrow that here.
                return uid  # type: ignore[no-any-return]
    except Exception:
        return None
    return None


__all__ = [
    "bind_request_context",
    "clear_request_context",
    "configure_structlog",
    "get_logger",
    "install_flask_request_hooks",
]
