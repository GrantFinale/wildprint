"""Tests for :mod:`review_app.limits`.

Uses Flask-Limiter's in-memory storage backend (the default when
``REDIS_URL`` is unset) so tests don't need Redis.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from flask import Flask, jsonify

from review_app import limits as limits_module
from review_app.limits import (
    cart_limit,
    is_admin_request,
    is_webhook_request,
    limiter,
    render_limit,
)


@pytest.fixture(autouse=True)
def _isolated_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset limiter state between tests + force in-memory storage."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    # Hammer the global state so each test starts clean.
    limits_module._initialized = False
    yield
    # Best-effort reset; ``Limiter.reset`` raises if no storage attached
    # (i.e. the test never called init_app).
    try:
        limiter.reset()
    except (AssertionError, AttributeError):
        pass


def _make_app(*, low_render: bool = False, low_cart: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    if low_render:
        # Make tests fast by allowing very few hits.
        app.config["RATELIMIT_DEFAULT"] = "100/minute"
    limits_module.init_app(app)
    return app


def test_render_endpoint_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_RENDER_PER_MIN", "2")
    app = _make_app()

    @app.route("/render", methods=["POST"])
    @render_limit()
    def render_view() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    assert client.post("/render").status_code == 200
    assert client.post("/render").status_code == 200
    assert client.post("/render").status_code == 429


def test_cart_endpoint_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_CART_PER_MIN", "3")
    app = _make_app()

    @app.route("/cart", methods=["POST"])
    @cart_limit()
    def cart_view() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    assert client.post("/cart").status_code == 200
    assert client.post("/cart").status_code == 200
    assert client.post("/cart").status_code == 200
    assert client.post("/cart").status_code == 429


def test_admin_endpoints_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/admin/*`` routes bypass the limiter entirely."""
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MIN", "1")
    app = _make_app()

    @app.route("/admin/foo")
    def admin_view() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    # Way more than the global limit; admin must stay 200.
    for _ in range(5):
        assert client.get("/admin/foo").status_code == 200


def test_webhook_endpoints_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Webhook receivers bypass the limiter (signed/idempotent)."""
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MIN", "1")
    app = _make_app()

    @app.route("/api/stripe/webhook", methods=["POST"])
    def stripe_webhook() -> object:
        return jsonify({"ok": True})

    @app.route("/api/prodigi/webhook", methods=["POST"])
    def prodigi_webhook() -> object:
        return jsonify({"ok": True})

    client = app.test_client()
    for _ in range(5):
        assert client.post("/api/stripe/webhook").status_code == 200
        assert client.post("/api/prodigi/webhook").status_code == 200


def test_render_limit_string_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_RENDER_PER_MIN", "42")
    assert limits_module.render_limit_string() == "42/minute"


def test_invalid_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_CART_PER_MIN", "not-a-number")
    assert limits_module.cart_limit_string() == "30/minute"


def test_is_admin_request_outside_request_returns_false() -> None:
    """Helpers don't crash when called outside a request context."""
    assert is_admin_request() is False
    assert is_webhook_request() is False
