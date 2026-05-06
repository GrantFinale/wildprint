"""Flask integration for the SQLAlchemy scoped session.

Wires the `scoped_session` to Flask's app context lifecycle so that each
request gets its own Session and the Session is removed (returned to the
pool) on teardown.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from review_app import db as _db

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: Flask) -> None:
    """Register the SQLAlchemy session teardown on the given Flask app.

    Idempotent: safe to call multiple times. Does NOT touch any other Flask
    config — by design, this is the minimal wiring point so it can be added
    to the existing monolithic `app.py` without disturbing anything else.

    The teardown is a no-op for requests that never opened a session, so
    routes that don't touch the DB don't pay the cost of building an engine
    (and don't crash if `DATABASE_URL` is malformed or unreachable).
    """
    @app.teardown_appcontext
    def _shutdown_session(exception: BaseException | None) -> None:
        # Probe the module-level singleton without triggering creation.
        # If no request on this process has ever called get_scoped_session(),
        # there is nothing to roll back or remove.
        scoped = _db._scoped_session
        if scoped is None:
            return
        if exception is not None:
            scoped.rollback()
        scoped.remove()


__all__ = ["init_app"]
