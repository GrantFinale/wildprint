"""Three-tier render system (Phase 2).

Wraps the deterministic ``poster_layout`` renderer with:

* **Tier 1 (thumbnail)** — 400 px JPEG, no watermark, public, lazy.
* **Tier 2 (preview)** — 2400 px JPEG (with 1800 px srcset variant), watermarked,
  public, generated once per "generate" click.
* **Tier 3 (print)** — 5400 x 7200 PNG, no watermark, private, queued via RQ
  after Stripe ``payment_intent.succeeded``.

All tiers derive from one canonical :class:`RenderSpec`. The spec hash is the
cache key — repeat requests for the same spec are cache hits.

Public API
----------

* ``RenderSpec`` — the Pydantic model that names a poster.
* ``render_tier(spec, tier, ...)`` — produce the bytes for one tier.
* ``get_or_render_tier(session, spec, tier, ...)`` — cache-aware variant that
  consults ``render_outputs`` and uploads to DO Spaces on miss.
* ``init_app(app)`` — Flask wiring no-op stub. Wire in ``review_app/app.py``::

      from review_app.render import init_app as init_render
      init_render(app)

  (Phase 2 leaves this as a logging-only no-op so the existing app file
  stays untouched.)

See ``docs/render-tiers.md`` for budgets, caching semantics, and the
``renderer_version`` bump procedure.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from review_app.render.cache import TierResult, get_or_render_tier
from review_app.render.renderer import render_tier
from review_app.render.spec import RENDERER_VERSION_DEFAULT, RenderSpec
from review_app.render.tiers import TIER_CONFIG, TIER_PREVIEW, TIER_PRINT, TIER_THUMB

if TYPE_CHECKING:
    from flask import Flask


_log = logging.getLogger(__name__)


def init_app(app: Flask) -> bool:
    """Log render-system status. No mutation of ``app``.

    Mirrors :func:`review_app.storage.init_app`. Returns True so that callers
    treating the return value as a readiness flag don't have to special-case
    Phase 2 (which has no init-time work to do — the renderer is lazily
    constructed inside :func:`render_tier`).
    """
    _log.info(
        "Render system ready (tiers configured: %s)",
        sorted(TIER_CONFIG.keys()),
    )
    return True


__all__ = [
    "RENDERER_VERSION_DEFAULT",
    "TIER_CONFIG",
    "TIER_PREVIEW",
    "TIER_PRINT",
    "TIER_THUMB",
    "RenderSpec",
    "TierResult",
    "get_or_render_tier",
    "init_app",
    "render_tier",
]
