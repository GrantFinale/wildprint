"""Settings sub-blueprint — Users / API keys / Integrations / Audit log / My account.

Importing this module registers all five Settings routes on the parent
``admin_bp`` (defined in :mod:`review_app.admin.routes`). Handlers live in
:mod:`review_app.admin.settings.routes`.
"""
from __future__ import annotations

from review_app.admin.settings import routes  # noqa: F401  side-effect import

__all__: list[str] = []
