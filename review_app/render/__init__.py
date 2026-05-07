"""Three-tier render system (Phase 2) — public API.

Modules being assembled across commits 2-7:

* ``spec.py`` — :class:`RenderSpec` (this commit)
* ``db_models.py`` — SQLAlchemy ORM rows (this commit)
* ``tiers.py`` — tier configuration (next commit)
* ``watermark.py`` — diagonal watermark (next commit)
* ``renderer.py`` — tier render wrapper
* ``cache.py`` — cache-aware dispatcher
* ``jobs.py`` — RQ job functions
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from review_app.render.spec import RENDERER_VERSION_DEFAULT, RenderSpec

if TYPE_CHECKING:
    from flask import Flask


_log = logging.getLogger(__name__)


def init_app(app: "Flask") -> bool:
    """Log render-system status. No mutation of ``app``.

    Phase 2 stub — wire from `review_app/app.py` when the render module
    finishes assembly. Returns True so callers don't have to special-case.
    """
    _log.info("Render system bootstrapping (Phase 2)")
    return True


__all__ = [
    "RENDERER_VERSION_DEFAULT",
    "RenderSpec",
    "init_app",
]
