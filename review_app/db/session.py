"""Flask integration for the SQLAlchemy scoped session.

Wires the `scoped_session` to Flask's app context lifecycle so that each
request gets its own Session and the Session is removed (returned to the
pool) on teardown.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from review_app.db import get_scoped_session

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: "Flask") -> None:
    """Register the SQLAlchemy session teardown on the given Flask app.

    Idempotent: safe to call multiple times. Does NOT touch any other Flask
    config — by design, this is the minimal wiring point so it can be added
    to the existing monolithic `app.py` without disturbing anything else.
    """
    @app.teardown_appcontext
    def _shutdown_session(exception: Optional[BaseException]) -> None:
        session = get_scoped_session()
        if exception is not None:
            session.rollback()
        session.remove()


__all__ = ["init_app"]
