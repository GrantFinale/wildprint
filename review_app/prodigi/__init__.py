"""Prodigi v4.0 print API client + webhook receiver.

Phase 1 of the fishingposter.com Prodigi integration. This package owns:

* Pydantic v2 models that mirror the Prodigi API surface
  (`review_app.prodigi.models`)
* A typed httpx-based HTTP client with retry, idempotency, and structured
  logging (`review_app.prodigi.client`)
* SQLAlchemy ORM models for the four Prodigi tables
  (`review_app.prodigi.db_models`)
* The webhook receiver blueprint with the dedupe + re-fetch pattern
  (`review_app.prodigi.webhooks`)
* The nightly quote refresh job (`review_app.prodigi.quote_refresh`)

Public surface:
    - `init_app(app)`: register the webhook blueprint with a Flask app.
    - `get_default_client()`, `get_sandbox_client()`, `get_live_client()`:
      factory helpers, lazy.
    - `ProdigiClient`, `ProdigiClientError`: the client class + exception.

`init_app(app)` is intentionally not called from `review_app/app.py` yet.
The wiring snippet to add (Phase 2 work) is documented in
`docs/prodigi-client.md`:

    from review_app.prodigi import init_app as _init_prodigi
    _init_prodigi(app)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from review_app.prodigi.client import (
    ProdigiClient,
    ProdigiClientError,
    get_default_client,
    get_live_client,
    get_sandbox_client,
)

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: Flask) -> None:
    """Register the Prodigi webhook blueprint on a Flask app.

    Idempotent — safe to call multiple times. The blueprint is registered
    under the URL prefix `/webhook/prodigi`.
    """
    from review_app.prodigi.webhooks import prodigi_bp

    # Avoid double-registration in tests that boot the app twice.
    if "prodigi" not in app.blueprints:
        app.register_blueprint(prodigi_bp)


__all__ = [
    "ProdigiClient",
    "ProdigiClientError",
    "get_default_client",
    "get_live_client",
    "get_sandbox_client",
    "init_app",
]
