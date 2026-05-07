"""Per-request admin shell context — Prodigi env pill, notifications, build id.

The base template (``templates/admin/_base.html``) reads several global
values that aren't tied to a specific page: the Prodigi environment pill,
the notification bell count, the git release SHA in the footer. Rather
than threading them through every route's ``render_template()`` kwargs,
we register a :func:`context_processor` on :data:`admin_bp` that fills
them in once per request.

The values are intentionally cheap (env lookups, no DB) so this scales
to every admin page render.
"""
from __future__ import annotations

import os
from typing import Any


def _prodigi_env_pill() -> dict[str, str]:
    """Compute ``{label, css_class}`` for the topbar Prodigi env pill.

    Reads ``PRODIGI_ENV`` (or falls back to ``PRODIGI_ENVIRONMENT``):

    * ``sandbox`` / unset / dev    -> red ``SANDBOX`` pill
    * ``live`` / ``production``    -> green ``PROD`` pill
    """
    raw = (
        os.environ.get("PRODIGI_ENV")
        or os.environ.get("PRODIGI_ENVIRONMENT")
        or "sandbox"
    ).strip().lower()
    if raw in {"live", "production", "prod"}:
        return {"label": "PROD", "css_class": "env-pill env-pill--prod"}
    return {"label": "SANDBOX", "css_class": "env-pill env-pill--sandbox"}


def _build_id() -> str:
    """Short git SHA / release tag for the footer. Empty string when unset."""
    sha = os.environ.get("SENTRY_RELEASE") or os.environ.get("GIT_SHA") or ""
    if not sha:
        return ""
    # Show only the first 7 chars — enough for grep, doesn't crowd the UI.
    return sha[:7]


def shell_context() -> dict[str, Any]:
    """Context processor — exposes shell-wide globals to every admin template.

    Notification count is sourced from ``flask.g.admin_notifications`` so
    that a Phase 5 polling endpoint can populate it per-request; defaults
    to 0 when unset.
    """
    from flask import g

    return {
        "prodigi_env_pill": _prodigi_env_pill(),
        "admin_notifications": int(getattr(g, "admin_notifications", 0) or 0),
        "build_id": _build_id(),
    }


__all__ = ["shell_context"]
