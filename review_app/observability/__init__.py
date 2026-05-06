"""Observability package — structlog + Sentry, with PII scrubbing.

Public surface:

- ``init_app(app)`` — call once during Flask app construction (early,
  before other middleware). Configures structlog, initializes Sentry
  (no-op if ``SENTRY_DSN`` unset), installs Flask request hooks, and
  registers the test blueprint.
- ``get_logger(name)`` — return a structlog bound logger.
- ``bind_request_context(**fields)`` — bind extra fields to the current
  request's log context.
- ``scrub_pii(event, hint)`` — pure function used as Sentry's
  ``before_send`` hook; also exported for testability.

Sentry is *opt-in*. The package never crashes the app on missing
external dependencies (no DSN, no Flask-Login, no SQLAlchemy
integration, etc.).
"""
from __future__ import annotations

import contextlib
from typing import Any

from .logging import (
    bind_request_context,
    clear_request_context,
    configure_structlog,
    get_logger,
    install_flask_request_hooks,
)
from .pii import scrub_pii
from .routes import _obs_test_bp
from .sentry import init_sentry


def init_app(app: Any) -> None:
    """Wire the observability stack into a Flask app.

    Order matters: Sentry first (so any subsequent boot-time errors are
    captured), then structlog, then Flask hooks, then the test
    blueprint. Idempotent — safe to call from multiple entry points.
    """
    init_sentry()
    configure_structlog()
    install_flask_request_hooks(app)
    # Blueprint already registered — fine, init_app was called twice.
    with contextlib.suppress(AssertionError, ValueError):
        app.register_blueprint(_obs_test_bp)


__all__ = [
    "bind_request_context",
    "clear_request_context",
    "get_logger",
    "init_app",
    "scrub_pii",
]
