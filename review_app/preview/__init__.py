"""Phase 2 frame preview compositor — Flask blueprint package.

Public surface:
    - ``preview_bp``: the Flask Blueprint (URL prefix ``/preview``).
    - ``init_app(app)``: register the blueprint on a Flask app.

The blueprint is intentionally NOT registered automatically by importing
``review_app/app.py``. The wiring snippet (Phase 2 wiring step) is::

    from review_app.preview import init_app as init_preview
    init_preview(app)

Add that next to the other ``_init_*`` calls in ``review_app/app.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from review_app.preview.routes import preview_bp

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: Flask) -> None:
    """Register the preview blueprint on a Flask app.

    Idempotent — safe to call twice (used in tests that boot the app
    multiple times).
    """
    if "preview" not in app.blueprints:
        app.register_blueprint(preview_bp)


__all__ = ["init_app", "preview_bp"]
