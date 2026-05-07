"""Customer module — buyer accounts.

Phase 3a scaffolding. Models live in :mod:`review_app.customers.models`.
The ``init_app`` shim is a no-op for now; later phases will register
blueprints / CLI commands here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.customers.models import Customer

__all__ = ["Customer", "init_app"]


def init_app(app: Flask) -> None:
    """No-op wiring stub. Implementations land in later phases."""
    # Intentionally empty — the Customer model is registered against
    # Base.metadata at import time, which is all that's needed today.
    return None
