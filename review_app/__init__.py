"""Wildprint review app package.

Phase 0.7 — provide a ``create_app(testing=False)`` factory shim that
returns the existing monolithic Flask app instance defined in
``review_app.app``. We deliberately do NOT refactor ``app.py`` here: the
factory just imports the already-constructed module-level ``app`` and
applies test-mode overrides when requested.

Tests should call ``create_app(testing=True)``. Production code continues
to use ``review_app.app:app`` directly via ``flask --app review_app.app``
or ``python -m review_app.app``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def create_app(testing: bool = False) -> Flask:
    """Return the wildprint Flask app, optionally configured for tests.

    Parameters
    ----------
    testing:
        When ``True``, flips ``TESTING``/``DEBUG`` config flags and
        disables CSRF protection so the test client can post forms
        without juggling tokens.
    """
    # Import lazily so that simply importing the package (e.g. for
    # ``review_app.db`` or ``review_app.queue``) does not pull in the
    # entire monolithic Flask app and its heavyweight dependencies.
    from flask import Flask

    from review_app.app import app as flask_app

    if testing:
        flask_app.config.update(
            TESTING=True,
            DEBUG=False,
            WTF_CSRF_ENABLED=False,
            PROPAGATE_EXCEPTIONS=True,
        )

    assert isinstance(flask_app, Flask)
    return flask_app


__all__ = ["create_app"]
