"""Test-only Flask blueprint for verifying the Sentry integration.

The route deliberately raises an exception so we can confirm Sentry
captures it end-to-end during the Phase 0.12 smoke test. To prevent
accidental exposure on prod, the blueprint is gated by the env var
``OBS_TEST_ENABLED=true``. When disabled, the route returns 404 so it
isn't discoverable.

A future pass (after Phase 0.6 ships admin auth) will additionally
gate this with ``@requires_role('admin')``.
"""
from __future__ import annotations

import os

from flask import Blueprint, abort

_obs_test_bp: Blueprint = Blueprint("_obs_test", __name__)


@_obs_test_bp.route("/admin/_sentry_test", methods=["GET"])
def sentry_test() -> None:
    """Raise a RuntimeError so Sentry can capture and tag it."""
    if os.getenv("OBS_TEST_ENABLED") != "true":
        abort(404)
    raise RuntimeError("intentional test exception for Sentry")


__all__ = ["_obs_test_bp"]
