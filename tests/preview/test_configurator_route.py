"""Tests for the /preview blueprint routes."""
from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture()
def preview_app() -> Iterator[Flask]:
    """A minimal Flask app with just the preview blueprint registered.

    We avoid booting the full ``review_app.app`` module here because it
    has side-effects (DB init, observability, etc.) that complicate the
    unit-level test. The preview blueprint is self-contained.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(
            os.path.dirname(__file__), "..", "..", "review_app", "templates"
        ),
        static_folder=os.path.join(
            os.path.dirname(__file__), "..", "..", "review_app", "static"
        ),
    )
    app.config["TESTING"] = True

    from review_app.preview import init_app

    init_app(app)
    # Re-register safe — exercises the idempotency guard.
    init_app(app)

    yield app


@pytest.fixture()
def client(preview_app: Flask):
    return preview_app.test_client()


# ---------------------------------------------------------------------------
# /preview/_demo
# ---------------------------------------------------------------------------


def test_demo_route_returns_404_when_flag_disabled(client, monkeypatch) -> None:
    monkeypatch.delenv("PREVIEW_DEMO_ENABLED", raising=False)
    res = client.get("/preview/_demo")
    assert res.status_code == 404


def test_demo_route_returns_200_when_flag_enabled(client, monkeypatch) -> None:
    monkeypatch.setenv("PREVIEW_DEMO_ENABLED", "true")
    # Patch the SKU price lookup so we don't need a real DB.
    with patch("review_app.preview.routes._load_sku_prices", return_value={}):
        res = client.get("/preview/_demo")
    assert res.status_code == 200, res.data.decode("utf-8", errors="replace")[:300]
    body = res.data.decode("utf-8")
    # Page contains the configurator scaffolding.
    assert "<canvas" in body
    assert 'id="preview-canvas"' in body
    assert 'id="size-select"' in body
    assert "finish-picker" in body
    # Default selections are wired into the data-* attributes.
    assert 'data-default-size="16x20"' in body
    assert 'data-default-finish="brown"' in body


def test_demo_route_falsy_flag_values_404(client, monkeypatch) -> None:
    for val in ["", "0", "false", "no", "False"]:
        monkeypatch.setenv("PREVIEW_DEMO_ENABLED", val)
        assert client.get("/preview/_demo").status_code == 404, val


# ---------------------------------------------------------------------------
# /preview/<spec_hash>
# ---------------------------------------------------------------------------


def test_preview_route_with_spec_hash_renders_template_with_skus(
    client, monkeypatch
) -> None:
    """When the render_outputs lookup returns a URL, the page renders."""
    fake_url = "https://cdn.example/previews/abc.jpg"
    fake_prices = {
        "cf-16x20-brown": 4900,
        "cf-16x20-black": 4900,
        "cf-12x16-brown": 3900,
    }
    spec_hash = "abc123def456"

    with patch(
        "review_app.preview.routes._lookup_tier2_preview_url",
        return_value=fake_url,
    ), patch(
        "review_app.preview.routes._load_sku_prices",
        return_value=fake_prices,
    ):
        res = client.get(f"/preview/{spec_hash}")

    assert res.status_code == 200
    body = res.data.decode("utf-8")
    assert fake_url in body
    assert "data-poster-preview-url=" in body
    # Prices wired into swatch buttons (data-price-cents-12X16, 16X20, etc.)
    assert "4900" in body
    # Walnut display override is applied to the brown swatch label.
    assert "Walnut" in body


def test_preview_route_404_when_render_spec_missing(client, monkeypatch) -> None:
    """When the render_outputs lookup returns None, the route 404s."""
    with patch(
        "review_app.preview.routes._lookup_tier2_preview_url", return_value=None
    ):
        res = client.get("/preview/abc123def456")
    assert res.status_code == 404


def test_preview_route_404_for_invalid_spec_hash(client) -> None:
    """Non-hex / too-short spec hashes are rejected before DB lookup."""
    # Routes that contain '_' would match _demo / _health, so use other invalids.
    for bad in ["xyz", "abc", "../etc/passwd"]:
        res = client.get(f"/preview/{bad}")
        assert res.status_code == 404, bad


# ---------------------------------------------------------------------------
# /preview/data/frame_skus.json
# ---------------------------------------------------------------------------


def test_serve_frame_skus_returns_json(client) -> None:
    res = client.get("/preview/data/frame_skus.json")
    assert res.status_code == 200
    assert res.mimetype == "application/json"
    import json as _json

    data = _json.loads(res.data)
    assert isinstance(data, list)
    assert len(data) == 32


# ---------------------------------------------------------------------------
# /preview/_health
# ---------------------------------------------------------------------------


def test_health_endpoint(client) -> None:
    res = client.get("/preview/_health")
    assert res.status_code == 200
    assert res.json == {"ok": True, "sku_count": 32}
