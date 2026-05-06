"""Sentry SDK initialization with safe no-op when DSN is unset.

Phase 0.9 ships *without* a Sentry account. `init_sentry()` must be
callable in every environment — if `SENTRY_DSN` is missing it logs an
INFO line and returns. Once a DSN is provisioned, just set the env var
and redeploy; no code changes required.

Also wires the Flask, SQLAlchemy and RQ integrations when those
optional dependencies are importable.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from .pii import scrub_pii

_log = logging.getLogger(__name__)

_INITIALIZED: bool = False


def _detect_release() -> str:
    """Resolve a release identifier, preferring `SENTRY_RELEASE` env var.

    Falls back to `git rev-parse --short HEAD`, then to "unknown" if git
    is unavailable (e.g. inside a slim Docker image without .git).
    """
    explicit = os.getenv("SENTRY_RELEASE")
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _build_integrations() -> list[Any]:
    """Return the list of optional Sentry integrations available in this env."""
    integrations: list[Any] = []
    try:
        from sentry_sdk.integrations.flask import FlaskIntegration

        integrations.append(FlaskIntegration())
    except Exception:
        pass
    try:
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        integrations.append(SqlalchemyIntegration())
    except Exception:
        pass
    try:
        from sentry_sdk.integrations.rq import RqIntegration

        integrations.append(RqIntegration())
    except Exception:
        pass
    return integrations


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Sentry `before_send` hook — strips PII from every outgoing event."""
    return scrub_pii(event, hint)


def init_sentry() -> bool:
    """Initialize Sentry if `SENTRY_DSN` is set. Idempotent.

    Returns True when the SDK was actually initialized, False when we
    no-opped (no DSN, SDK not installed, or already initialized).
    """
    global _INITIALIZED
    if _INITIALIZED:
        return False

    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        _log.info("Sentry disabled (no DSN)")
        _INITIALIZED = True  # latch so we don't log on every gunicorn worker re-init
        return False

    try:
        import sentry_sdk
    except ImportError:
        _log.info("Sentry disabled (sentry-sdk not installed)")
        _INITIALIZED = True
        return False

    try:
        traces = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    except ValueError:
        traces = 0.1

    sentry_sdk.init(
        dsn=dsn,
        integrations=_build_integrations(),
        traces_sample_rate=traces,
        environment=os.getenv("SENTRY_ENVIRONMENT", "dev"),
        release=_detect_release(),
        send_default_pii=False,
        before_send=_before_send,
    )
    _INITIALIZED = True
    _log.info("Sentry initialized")
    return True


__all__ = ["init_sentry"]
