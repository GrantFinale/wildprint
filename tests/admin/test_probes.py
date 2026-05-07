"""Tests for :mod:`review_app.admin.settings.probes`.

Each probe is mocked at the HTTP boundary (httpx, boto3, stripe SDK).
Real network calls only happen with ``--integration``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from review_app.admin.settings import probes as probes_module


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the probes cache + clear env vars between tests."""
    for env in (
        "STRIPE_SECRET_KEY",
        "RESEND_API_KEY",
        "PRODIGI_SANDBOX_API_KEY",
        "SMARTY_AUTH_ID",
        "SMARTY_AUTH_TOKEN",
        "SPACES_THUMBS_BUCKET",
    ):
        monkeypatch.delenv(env, raising=False)
    probes_module.reset_cache()


# ---------------------------------------------------------------------------
# Happy paths (mocked)
# ---------------------------------------------------------------------------
def test_probe_prodigi_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODIGI_SANDBOX_API_KEY", "k")

    fake_resp = MagicMock(status_code=200)
    with patch("httpx.get", return_value=fake_resp):
        result = probes_module.probe_prodigi()
    assert result.ok is True
    assert result.error is None
    assert result.latency_ms is not None


def test_probe_resend_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_dummy")

    fake_resp = MagicMock(status_code=200)
    with patch("httpx.get", return_value=fake_resp):
        result = probes_module.probe_resend()
    assert result.ok is True


def test_probe_smarty_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTY_AUTH_ID", "id")
    monkeypatch.setenv("SMARTY_AUTH_TOKEN", "tok")

    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = [{"delivery_point": "DPV1"}]
    with patch("httpx.get", return_value=fake_resp):
        result = probes_module.probe_smarty()
    assert result.ok is True
    assert result.note is not None


def test_probe_spaces_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPACES_THUMBS_BUCKET", "wp-thumbs")

    fake_client = MagicMock()
    fake_client.list_objects_v2.return_value = {"KeyCount": 5}
    with patch("review_app.storage.spaces._client", return_value=fake_client):
        result = probes_module.probe_spaces()
    assert result.ok is True
    assert "5" in (result.note or "")


def test_probe_stripe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    fake_account = MagicMock(id="acct_123")
    with patch("stripe.Account.retrieve", return_value=fake_account):
        result = probes_module.probe_stripe()
    assert result.ok is True
    assert "acct_123" in (result.note or "")


# ---------------------------------------------------------------------------
# Sad paths
# ---------------------------------------------------------------------------
def test_probe_prodigi_missing_env() -> None:
    result = probes_module.probe_prodigi()
    assert result.ok is False
    assert "PRODIGI_SANDBOX_API_KEY" in (result.error or "")


def test_probe_stripe_handles_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    with patch("stripe.Account.retrieve", side_effect=RuntimeError("auth failed")):
        result = probes_module.probe_stripe()
    assert result.ok is False
    assert "auth failed" in (result.error or "")


# ---------------------------------------------------------------------------
# Aggregator + cache
# ---------------------------------------------------------------------------
def test_probe_all_runs_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five probes execute and produce a result each."""
    # Set every env var so each probe reaches its mocked HTTP layer.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk")
    monkeypatch.setenv("RESEND_API_KEY", "re")
    monkeypatch.setenv("PRODIGI_SANDBOX_API_KEY", "pk")
    monkeypatch.setenv("SMARTY_AUTH_ID", "id")
    monkeypatch.setenv("SMARTY_AUTH_TOKEN", "tok")
    monkeypatch.setenv("SPACES_THUMBS_BUCKET", "wp")

    fake_http = MagicMock(status_code=200)
    fake_http.json.return_value = []
    fake_account = MagicMock(id="acct_z")
    fake_spaces = MagicMock()
    fake_spaces.list_objects_v2.return_value = {"KeyCount": 0}

    with patch("httpx.get", return_value=fake_http), patch(
        "stripe.Account.retrieve", return_value=fake_account
    ), patch(
        "review_app.storage.spaces._client", return_value=fake_spaces
    ):
        results = probes_module.probe_all()

    assert set(results.keys()) == set(probes_module.PROBES.keys())
    assert all(r.ok for r in results.values())


def test_probe_caches_result_for_5_min(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODIGI_SANDBOX_API_KEY", "k")

    fake_resp = MagicMock(status_code=200)
    with patch("httpx.get", return_value=fake_resp) as mock_get:
        probes_module.probe_prodigi()
        probes_module.probe_prodigi()
        probes_module.probe_prodigi()

    # 3 calls to the public API but only 1 outbound HTTP request.
    assert mock_get.call_count == 1


def test_reset_cache_forces_re_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODIGI_SANDBOX_API_KEY", "k")

    fake_resp = MagicMock(status_code=200)
    with patch("httpx.get", return_value=fake_resp) as mock_get:
        probes_module.probe_prodigi()
        probes_module.reset_cache()
        probes_module.probe_prodigi()

    assert mock_get.call_count == 2
