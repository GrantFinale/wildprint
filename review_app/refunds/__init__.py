"""Refunds module — Stripe refund + Prodigi cancel records.

Phase 3a scaffolding: model only. Refund orchestration lives in later phases.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

from review_app.refunds.models import VALID_REFUND_STATUSES, Refund

__all__ = ["VALID_REFUND_STATUSES", "Refund", "init_app"]


def init_app(app: Flask) -> None:
    """No-op wiring stub. Implementations land in later phases."""
    return None
