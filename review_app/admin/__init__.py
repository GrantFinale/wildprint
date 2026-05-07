"""Admin shell blueprint — Flask-Login + RBAC gated /admin/* pages.

Phase 4a deliverable. Provides the new chrome (sidebar, topbar, role gating)
and migrates the existing legacy ``/admin*`` species/backgrounds/sizing pages
into a unified shell. The legacy single-page ``/admin`` (basic-auth gated)
keeps working: legacy routes 301-redirect to the new shell URLs.

Public surface
--------------

* :data:`admin_bp` — the top-level Flask :class:`~flask.Blueprint` mounted at
  ``/admin``. It re-exports the dashboard route plus nested sub-blueprints
  (catalog, settings, search).
* :func:`init_app` — register the blueprint on a Flask app. Idempotent.

The wiring pass in ``review_app/app.py`` calls :func:`init_app` after
:func:`review_app.auth.init_app` so that Flask-Login is already attached
when our routes are first imported (the ``@requires_role`` decorator looks
up the login manager via the app extension dict).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from review_app.admin.routes import admin_bp

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: Flask) -> None:
    """Register :data:`admin_bp` on ``app``.

    Idempotent — safe to call multiple times. The auth blueprint must
    already be registered (so ``url_for('auth.login')`` resolves inside
    the role-gating decorator); the wiring block in
    ``review_app/app.py`` enforces that ordering.
    """
    if "admin" not in app.blueprints:
        app.register_blueprint(admin_bp)


__all__ = ["admin_bp", "init_app"]
