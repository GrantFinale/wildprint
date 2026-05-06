"""Tests for the Phase 0.9 observability package.

Unit tests run without any external dependencies — no Sentry account,
no Redis, no DB. Integration tests that actually hit Sentry are marked
``@pytest.mark.integration`` and skipped unless ``SENTRY_DSN`` is set.
"""
from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import pytest
from flask import Flask

from review_app.observability import (
    bind_request_context,
    get_logger,
    init_app,
    scrub_pii,
)
from review_app.observability.pii import REDACTED


# ---------------------------------------------------------------------------
# init_app — must succeed without SENTRY_DSN
# ---------------------------------------------------------------------------
def test_init_app_without_sentry_dsn_doesnt_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    app = Flask(__name__)
    init_app(app)  # must not raise


def test_init_app_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling init_app twice (e.g. gunicorn worker reloads) must be safe."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    app = Flask(__name__)
    init_app(app)
    init_app(app)


# ---------------------------------------------------------------------------
# get_logger — returns a usable structlog logger
# ---------------------------------------------------------------------------
def test_get_logger_returns_logger() -> None:
    log = get_logger("wildprint.test")
    # structlog's BoundLoggerLazyProxy exposes the standard methods
    for method in ("debug", "info", "warning", "error", "critical", "bind"):
        assert hasattr(log, method), f"logger missing {method}()"


# ---------------------------------------------------------------------------
# Request context — request_id is bound automatically per Flask request
# ---------------------------------------------------------------------------
def test_request_id_bound_to_log_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A log line emitted inside a request must carry a request_id key."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")  # JSON renderer => parseable

    app = Flask(__name__)
    init_app(app)

    captured: dict[str, Any] = {}

    @app.route("/_test")
    def _route() -> str:
        log = get_logger("wildprint.test.route")
        # Capture the structlog contextvars for assertion.
        import structlog

        captured.update(structlog.contextvars.get_contextvars())
        log.info("hello_from_test")
        return "ok"

    client = app.test_client()
    resp = client.get("/_test", headers={"X-Request-Id": "fixed-id-abc"})
    assert resp.status_code == 200
    assert captured.get("request_id") == "fixed-id-abc"
    assert captured.get("http_method") == "GET"
    assert captured.get("http_path") == "/_test"


def test_request_id_generated_when_header_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    app = Flask(__name__)
    init_app(app)

    captured: dict[str, Any] = {}

    @app.route("/_test2")
    def _route() -> str:
        import structlog

        captured.update(structlog.contextvars.get_contextvars())
        return "ok"

    client = app.test_client()
    client.get("/_test2")
    rid = captured.get("request_id")
    assert isinstance(rid, str) and len(rid) >= 32  # uuid4 string is 36 chars


# ---------------------------------------------------------------------------
# scrub_pii — pure function, exhaustive cases
# ---------------------------------------------------------------------------
def test_scrub_pii_redacts_email_field() -> None:
    event = {"user": {"email": "alice@example.com", "id": 7}}
    out = scrub_pii(event, None)
    assert out["user"]["email"] == REDACTED
    assert out["user"]["id"] == 7  # non-PII passes through


def test_scrub_pii_redacts_nested_password_field() -> None:
    event = {
        "request": {
            "data": {
                "password": "hunter2",
                "remember_me": True,
            }
        }
    }
    out = scrub_pii(event, None)
    assert out["request"]["data"]["password"] == REDACTED
    assert out["request"]["data"]["remember_me"] is True


def test_scrub_pii_leaves_innocuous_fields() -> None:
    event = {
        "level": "error",
        "message": "boom",
        "tags": {"release": "abc123", "env": "dev"},
        "extra": {"poster_id": "p_42", "species_count": 5},
    }
    out = scrub_pii(event, None)
    assert out == event  # nothing should have changed
    assert out is not event  # but it's a fresh copy


def test_scrub_pii_redacts_ipv4_string() -> None:
    event = {"server_name": "host-203.0.113.42-prod"}
    out = scrub_pii(event, None)
    assert REDACTED in out["server_name"]
    assert "203.0.113.42" not in out["server_name"]


def test_scrub_pii_full_redaction_acceptance_case() -> None:
    """Spec-mandated case from the Phase 0.9 deliverables list."""
    event: dict[str, Any] = {
        "user": {"email": "x@y.com"},
        "extra": {"api_key": "sk_..."},
    }
    out = scrub_pii(event, None)
    assert out == {
        "user": {"email": REDACTED},
        "extra": {"api_key": REDACTED},
    }


def test_scrub_pii_redacts_token_and_authorization_and_cookie() -> None:
    event = {
        "headers": {
            "Authorization": "Bearer abc.def.ghi",
            "Cookie": "session=xyz",
            "X-Token": "t_123",
            "User-Agent": "pytest",
        }
    }
    out = scrub_pii(event, None)
    assert out["headers"]["Authorization"] == REDACTED
    assert out["headers"]["Cookie"] == REDACTED
    assert out["headers"]["X-Token"] == REDACTED
    assert out["headers"]["User-Agent"] == "pytest"


def test_scrub_pii_walks_lists() -> None:
    event = {"breadcrumbs": [{"data": {"email": "a@b.com"}}, {"data": {"ok": 1}}]}
    out = scrub_pii(event, None)
    assert out["breadcrumbs"][0]["data"]["email"] == REDACTED
    assert out["breadcrumbs"][1]["data"]["ok"] == 1


def test_scrub_pii_does_not_mutate_input() -> None:
    event = {"user": {"email": "a@b.com"}}
    original = json.loads(json.dumps(event))
    scrub_pii(event, None)
    assert event == original


# ---------------------------------------------------------------------------
# bind_request_context — works outside Flask too (e.g. RQ jobs)
# ---------------------------------------------------------------------------
def test_bind_request_context_outside_flask() -> None:
    import structlog

    structlog.contextvars.clear_contextvars()
    bind_request_context(job_id="job_1", actor="worker")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("job_id") == "job_1"
    assert ctx.get("actor") == "worker"
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Integration — only runs when a real DSN is provided
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("SENTRY_DSN"),
    reason="SENTRY_DSN not set; skipping live Sentry integration",
)
def test_sentry_actually_initializes_with_real_dsn() -> None:
    from review_app.observability.sentry import init_sentry

    # Reset the latch so the test sees a fresh init.
    import review_app.observability.sentry as sentry_mod

    sentry_mod._INITIALIZED = False
    assert init_sentry() is True
