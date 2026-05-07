"""Catalog sub-blueprint — Species / Backgrounds / Sizing / Frame SKUs / Lakes / Render presets.

Importing this module registers all six Catalog routes on the parent
``admin_bp`` (defined in :mod:`review_app.admin.routes`). The actual
handlers live in :mod:`review_app.admin.catalog.routes`.
"""
from __future__ import annotations

from review_app.admin.catalog import routes  # noqa: F401  side-effect import

__all__: list[str] = []
