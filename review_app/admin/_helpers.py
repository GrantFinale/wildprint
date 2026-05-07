"""Small shared helpers for admin route handlers.

Kept private (leading underscore module name) — these are implementation
details consumed by ``review_app.admin.routes`` and the sub-blueprints,
not part of the package's public API.
"""
from __future__ import annotations

import os
from typing import Any

from flask_login import current_user


def current_role() -> str | None:
    """Return the current Flask-Login user's role, or a sensible default.

    Behavior:
      * When ``ADMIN_AUTH_ENABLED`` is unset (shadow mode), every admin
        page is accessible without a login. Treat the implicit user as
        ``admin`` so the sidebar shows everything during the cutover.
      * When auth is enabled and the user is anonymous, return ``None``
        — callers (e.g. the sidebar) hide everything.
      * Otherwise return ``current_user.role``.
    """
    if not _auth_enabled():
        return "admin"
    if not current_user.is_authenticated:
        return None
    return getattr(current_user, "role", None)


def _auth_enabled() -> bool:
    return os.environ.get("ADMIN_AUTH_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def crumbs(*pairs: tuple[str, str | None]) -> list[dict[str, Any]]:
    """Build a breadcrumb list from ``(label, url_or_None)`` pairs.

    Example::

        crumbs(("Admin", "/admin"), ("Catalog", None), ("Species", None))
        -> [{"label": "Admin", "url": "/admin"},
            {"label": "Catalog", "url": None},
            {"label": "Species", "url": None}]

    The base template renders the trailing item as plain text (current
    page) and the rest as anchors when ``url`` is set.
    """
    return [{"label": label, "url": url} for label, url in pairs]


__all__ = ["crumbs", "current_role"]
