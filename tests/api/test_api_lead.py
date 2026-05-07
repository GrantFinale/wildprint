"""Tests for the ``/api/lead`` endpoint after the ZIP-code cutover.

The lead form now collects a 5-digit US ZIP code. The endpoint resolves
that ZIP to a state via Smarty (with a static prefix-table fallback) and
redirects to ``/create?lake=...&state=...&email=...&zip=...``.

Backward compat: a legacy ``state=...`` body is still accepted (with a
warning log) so existing curl clients / cached pages don't break.
"""
from __future__ import annotations

from unittest import mock

import pytest
from flask.testing import FlaskClient

from review_app.addresses.smarty import SmartyError, SmartyResult


def _smarty_ok(state: str, *, city: str = "Ann Arbor", zip_code: str = "48104") -> SmartyResult:
    return SmartyResult(
        is_deliverable=True,
        dpv_match_code="Y",
        standardized_line1="",
        standardized_city=city,
        standardized_state=state,
        standardized_zip=zip_code,
        standardized_zip_plus4="",
        raw_response={},
    )


@pytest.fixture(autouse=True)
def _stub_save_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid hitting the leads.json file / customers DB during these tests."""
    monkeypatch.setattr(
        "review_app.app._save_lead",
        lambda email, lake_name, state_code: {"email": email, "lake_name": lake_name, "state": state_code},
    )
    monkeypatch.setattr("review_app.app._is_paid", lambda email: False)


def test_api_lead_accepts_zip_and_redirects_with_state(client: FlaskClient) -> None:
    """Happy path: ZIP-only body resolves to state via Smarty + redirects."""
    with mock.patch(
        "review_app.addresses.zip_resolver.verify_address",
        return_value=_smarty_ok("MI"),
    ):
        resp = client.post(
            "/api/lead",
            json={"email": "lake@example.com", "lake_name": "Lake Tahoe", "zip_code": "48104"},
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["state"] == "MI"
    assert body["zip_code"] == "48104"
    assert body["source"] == "smarty"
    redirect = body["redirect"]
    assert redirect.startswith("/create?")
    assert "state=MI" in redirect
    assert "lake=Lake+Tahoe" in redirect or "lake=Lake%20Tahoe" in redirect
    assert "zip=48104" in redirect


def test_api_lead_falls_back_to_static_lookup_when_smarty_unreachable(client: FlaskClient) -> None:
    """When Smarty raises, the static prefix table resolves the state."""
    with mock.patch(
        "review_app.addresses.zip_resolver.verify_address",
        side_effect=SmartyError("Smarty down"),
    ):
        # 48104 → MI in the static prefix table.
        resp = client.post(
            "/api/lead",
            json={"email": "fallback@example.com", "lake_name": "Lake Tahoe", "zip_code": "48104"},
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["state"] == "MI"
    assert body["source"] == "fallback"


def test_api_lead_rejects_4_digit_zip(client: FlaskClient) -> None:
    """ZIP must be exactly 5 digits — short ZIPs return 400."""
    resp = client.post(
        "/api/lead",
        json={"email": "x@example.com", "lake_name": "Lake Tahoe", "zip_code": "4810"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "ZIP" in (body.get("error") or "")


def test_api_lead_rejects_non_numeric_zip(client: FlaskClient) -> None:
    """Letters in the ZIP also fail validation."""
    resp = client.post(
        "/api/lead",
        json={"email": "x@example.com", "lake_name": "Lake Tahoe", "zip_code": "ABCDE"},
    )
    assert resp.status_code == 400


def test_api_lead_backwards_compatible_with_state_field(client: FlaskClient) -> None:
    """Older clients posting ``state`` directly still work (with a warning)."""
    resp = client.post(
        "/api/lead",
        json={"email": "old@example.com", "lake_name": "Lake Tahoe", "state": "MI"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "MI"
    assert body["source"] == "legacy_state"
    assert "state=MI" in body["redirect"]


def test_api_lead_requires_either_zip_or_state(client: FlaskClient) -> None:
    """No location info → 400."""
    resp = client.post(
        "/api/lead",
        json={"email": "x@example.com", "lake_name": "Lake Tahoe"},
    )
    assert resp.status_code == 400


def test_api_lead_smarty_returns_no_state_falls_back(client: FlaskClient) -> None:
    """If Smarty returns an empty state, the static table is consulted."""
    empty = SmartyResult(
        is_deliverable=False,
        dpv_match_code="",
        standardized_state="",
        raw_response={},
    )
    with mock.patch(
        "review_app.addresses.zip_resolver.verify_address",
        return_value=empty,
    ):
        # 89449 → NV in the static prefix table.
        resp = client.post(
            "/api/lead",
            json={"email": "x@example.com", "lake_name": "Lake Tahoe", "zip_code": "89449"},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "NV"
    assert body["source"] == "fallback"
