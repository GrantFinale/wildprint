"""Smarty US Street API client — unit + (gated) integration tests.

The integration test (`test_verify_address_live_smarty`) is marked with
``@pytest.mark.integration`` and only runs when pytest is invoked with
``--integration`` AND the Smarty creds are present in the environment.
Otherwise the conftest hook auto-skips it.
"""
from __future__ import annotations

import json
import os
import urllib.error
from typing import Any
from unittest import mock

import pytest

from review_app.addresses.smarty import (
    DELIVERABLE_DPV_CODES,
    SmartyError,
    SmartyResult,
    verify_address,
)


# ---------------------------------------------------------------------------
# Fixtures: response payloads modelled on real Smarty responses (trimmed).
# ---------------------------------------------------------------------------
def _candidate(dpv_code: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "delivery_line_1": "1 Apple Park Way",
        "last_line": "Cupertino CA 95014-2083",
        "components": {
            "primary_number": "1",
            "street_name": "Apple Park",
            "street_suffix": "Way",
            "city_name": "Cupertino",
            "state_abbreviation": "CA",
            "zipcode": "95014",
            "plus4_code": "2083",
        },
        "metadata": {
            "record_type": "S",
            "zip_type": "Standard",
            "county_name": "Santa Clara",
            "carrier_route": "C001",
            "rdi": "Commercial",
        },
        "analysis": {
            "dpv_match_code": dpv_code,
            "dpv_footnotes": "AABB",
        },
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _set_smarty_env(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Default to fake creds so verify_address() doesn't raise SmartyError.

    The live-API integration test (``@pytest.mark.integration``) is exempted
    so it can use the real credentials from the environment.
    """
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.setenv("SMARTY_AUTH_ID", "test-auth-id")
    monkeypatch.setenv("SMARTY_AUTH_TOKEN", "test-auth-token")


def _mock_urlopen(payload: list[dict[str, Any]] | dict[str, Any], *, status: int = 200) -> mock.MagicMock:
    """Build a urllib urlopen() mock returning ``payload`` as a JSON body."""
    body = json.dumps(payload).encode("utf-8")
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = body
    cm.__enter__.return_value.status = status
    return cm


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------
class TestVerifyAddressDeliverable:
    def test_returns_deliverable_for_valid_input(self) -> None:
        """DPV='Y' → is_deliverable=True + standardized fields populated."""
        cm = _mock_urlopen([_candidate("Y")])
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            result: SmartyResult = verify_address(
                line1="1 apple park way",
                line2=None,
                city="cupertino",
                state="ca",
                zip_code="95014",
            )
        assert result.is_deliverable is True
        assert result.dpv_match_code == "Y"
        assert result.standardized_line1 == "1 Apple Park Way"
        assert result.standardized_city == "Cupertino"
        assert result.standardized_state == "CA"
        assert result.standardized_zip == "95014"
        assert result.standardized_zip_plus4 == "2083"
        assert result.raw_response["candidates"][0]["analysis"]["dpv_match_code"] == "Y"

    def test_handles_secondary_missing_dpv_s(self) -> None:
        """DPV='S' → still deliverable (secondary unit unknown but ok)."""
        cm = _mock_urlopen([_candidate("S")])
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            result = verify_address("1 apple park way", None, "cupertino", "CA", "95014")
        assert result.is_deliverable is True
        assert result.dpv_match_code == "S"
        assert "S" in DELIVERABLE_DPV_CODES

    def test_handles_secondary_missing_dpv_d(self) -> None:
        """DPV='D' → deliverable; secondary missing-but-not-required."""
        cm = _mock_urlopen([_candidate("D")])
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            result = verify_address("1 apple park way", None, "cupertino", "CA", "95014")
        assert result.is_deliverable is True
        assert result.dpv_match_code == "D"


# ---------------------------------------------------------------------------
# Sad paths
# ---------------------------------------------------------------------------
class TestVerifyAddressUndeliverable:
    def test_marks_undeliverable_when_dpv_n(self) -> None:
        """DPV='N' → is_deliverable=False but result still parses."""
        cm = _mock_urlopen([_candidate("N")])
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            result = verify_address("999 nowhere ln", None, "Nowhereville", "ND", "99999")
        assert result.is_deliverable is False
        assert result.dpv_match_code == "N"

    def test_empty_candidates_array_returns_undeliverable(self) -> None:
        """Smarty returns [] for un-parseable garbage → not deliverable."""
        cm = _mock_urlopen([])
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            result = verify_address("garbage", None, "garbage", "GA", "99999")
        assert result.is_deliverable is False
        assert result.dpv_match_code == ""


class TestVerifyAddressErrors:
    def test_raises_smarty_error_on_4xx(self) -> None:
        """HTTP 401 (bad creds) → SmartyError with status_code preserved."""
        err_body = json.dumps({"errors": [{"message": "Unauthorized"}]}).encode()
        http_err = urllib.error.HTTPError(
            url="https://us-street.api.smarty.com/street-address",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=mock.MagicMock(read=lambda: err_body),
        )
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", side_effect=http_err
        ):
            with pytest.raises(SmartyError) as exc_info:
                verify_address("1 apple park way", None, "cupertino", "CA", "95014")
        assert exc_info.value.status_code == 401
        assert "401" in str(exc_info.value)

    def test_raises_smarty_error_on_5xx(self) -> None:
        """HTTP 502 (gateway) → SmartyError with status_code preserved."""
        http_err = urllib.error.HTTPError(
            url="https://us-street.api.smarty.com/street-address",
            code=502,
            msg="Bad Gateway",
            hdrs=None,  # type: ignore[arg-type]
            fp=mock.MagicMock(read=lambda: b""),
        )
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", side_effect=http_err
        ):
            with pytest.raises(SmartyError) as exc_info:
                verify_address("1 apple park way", None, "cupertino", "CA", "95014")
        assert exc_info.value.status_code == 502

    def test_raises_smarty_error_on_url_error(self) -> None:
        """Transport-level failure (DNS, connection refused) → SmartyError."""
        url_err = urllib.error.URLError("[Errno 61] Connection refused")
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", side_effect=url_err
        ):
            with pytest.raises(SmartyError):
                verify_address("1 apple park way", None, "cupertino", "CA", "95014")

    def test_raises_smarty_error_on_invalid_json(self) -> None:
        """Body isn't JSON → SmartyError surfaces the decode failure."""
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b"<html>oops</html>"
        cm.__enter__.return_value.status = 200
        with mock.patch(
            "review_app.addresses.smarty.urllib.request.urlopen", return_value=cm
        ):
            with pytest.raises(SmartyError):
                verify_address("1 apple park way", None, "cupertino", "CA", "95014")

    def test_missing_credentials_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No SMARTY_AUTH_ID / SMARTY_AUTH_TOKEN → SmartyError, not crash."""
        monkeypatch.delenv("SMARTY_AUTH_ID", raising=False)
        monkeypatch.delenv("SMARTY_AUTH_TOKEN", raising=False)
        with pytest.raises(SmartyError, match="SMARTY_AUTH"):
            verify_address("1 apple park way", None, "cupertino", "CA", "95014")

    def test_non_us_country_raises(self) -> None:
        """Smarty US Street is US-only — caller must filter upstream."""
        with pytest.raises(SmartyError, match="US-only"):
            verify_address(
                "10 Downing St", None, "London", "EN", "SW1A2AA", country="GB"
            )


# ---------------------------------------------------------------------------
# Live integration test (gated by --integration)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_verify_address_live_smarty() -> None:
    """Hit the real Smarty API once with a known-good address.

    Verifies that creds work and the response shape we parse against is
    still current. Skipped when ``SMARTY_AUTH_ID`` / ``SMARTY_AUTH_TOKEN``
    aren't in the live environment.
    """
    if not (os.environ.get("SMARTY_AUTH_ID") and os.environ.get("SMARTY_AUTH_TOKEN")):
        pytest.skip("SMARTY_AUTH_ID / SMARTY_AUTH_TOKEN not set in env.")
    # Apple Park is a known-deliverable address.
    result = verify_address("1 Apple Park Way", None, "Cupertino", "CA", "95014")
    assert result.is_deliverable is True
    assert result.dpv_match_code in DELIVERABLE_DPV_CODES
    assert result.standardized_state == "CA"
    assert result.standardized_zip == "95014"
