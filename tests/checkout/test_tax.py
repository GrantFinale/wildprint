"""Tests for :mod:`review_app.checkout.tax`.

The Stripe SDK is mocked at the ``stripe.tax.Calculation.create`` boundary
so these tests don't make real network calls.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from review_app.checkout import tax as tax_module


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the in-memory tax cache + reset the env flag between tests."""
    monkeypatch.delenv("STRIPE_TAX_ENABLED", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    tax_module.reset_cache()


def _line_items() -> list[dict[str, Any]]:
    return [
        {
            "amount": 7900,
            "quantity": 1,
            "reference": "ci-1",
            "tax_code": "txcd_99999999",
        }
    ]


def _address() -> dict[str, Any]:
    return {
        "line1": "1 Apple Park Way",
        "city": "Cupertino",
        "state": "CA",
        "postal_code": "95014",
        "country": "US",
    }


def test_tax_disabled_returns_zero() -> None:
    """When STRIPE_TAX_ENABLED is unset, returns enabled=False, 0 cents."""
    result = tax_module.compute_tax_for_session(_line_items(), _address())
    assert result.enabled is False
    assert result.tax_amount_cents == 0
    assert result.breakdown == []
    assert result.calculation_id is None


def test_tax_enabled_calls_stripe_and_returns_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled, calls stripe.tax.Calculation.create and parses the response."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    fake_calc = MagicMock()
    fake_calc.to_dict.return_value = {
        "id": "taxcalc_abc123",
        "tax_amount_exclusive": 689,
        "tax_breakdown": [
            {"jurisdiction": {"display_name": "California"}, "amount": 689},
        ],
    }

    with patch.object(tax_module, "_to_dict", side_effect=lambda obj: obj.to_dict() if hasattr(obj, "to_dict") else obj):  # noqa: SIM115
        with patch("stripe.tax.Calculation.create", return_value=fake_calc) as mock_create:
            result = tax_module.compute_tax_for_session(_line_items(), _address())

    assert mock_create.called
    assert result.enabled is True
    assert result.tax_amount_cents == 689
    assert result.calculation_id == "taxcalc_abc123"
    assert result.breakdown == [
        {"jurisdiction": {"display_name": "California"}, "amount": 689}
    ]


def test_tax_caches_for_same_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same line items + address only hits Stripe once within the TTL."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    fake_calc = MagicMock()
    fake_calc.to_dict.return_value = {
        "id": "taxcalc_xyz",
        "tax_amount_exclusive": 100,
        "tax_breakdown": [],
    }

    with patch.object(tax_module, "_to_dict", side_effect=lambda obj: obj.to_dict() if hasattr(obj, "to_dict") else obj):  # noqa: SIM115
        with patch("stripe.tax.Calculation.create", return_value=fake_calc) as mock_create:
            r1 = tax_module.compute_tax_for_session(_line_items(), _address())
            r2 = tax_module.compute_tax_for_session(_line_items(), _address())

    assert mock_create.call_count == 1
    assert r1 == r2


def test_tax_handles_stripe_error_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Stripe raises, we wrap it as TaxComputationError and record."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    with patch(
        "stripe.tax.Calculation.create",
        side_effect=RuntimeError("boom: address is invalid"),
    ):
        with pytest.raises(tax_module.TaxComputationError) as excinfo:
            tax_module.compute_tax_for_session(_line_items(), _address())

    assert "boom: address is invalid" in str(excinfo.value)
    errors = tax_module.recent_errors()
    assert len(errors) == 1
    assert "boom" in errors[0]["error"]


def test_tax_enabled_empty_cart_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with the flag on, an empty cart short-circuits to zero."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    result = tax_module.compute_tax_for_session([], _address())
    assert result.enabled is True
    assert result.tax_amount_cents == 0


def test_tax_non_us_country_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 5a is US-only; non-US shipping addresses are rejected."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")

    addr = dict(_address())
    addr["country"] = "CA"

    with pytest.raises(tax_module.TaxComputationError):
        tax_module.compute_tax_for_session(_line_items(), addr)


def test_tax_missing_secret_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling the feature without a Stripe secret raises a clear error."""
    monkeypatch.setenv("STRIPE_TAX_ENABLED", "true")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    with pytest.raises(tax_module.TaxComputationError) as excinfo:
        tax_module.compute_tax_for_session(_line_items(), _address())
    assert "STRIPE_SECRET_KEY" in str(excinfo.value)


def test_result_as_dict_round_trip() -> None:
    """The legacy ``result_as_dict`` helper preserves all fields."""
    r = tax_module.TaxResult(
        enabled=True,
        tax_amount_cents=42,
        breakdown=[{"foo": "bar"}],
        calculation_id="taxcalc_42",
    )
    assert tax_module.result_as_dict(r) == {
        "enabled": True,
        "tax_amount_cents": 42,
        "breakdown": [{"foo": "bar"}],
        "calculation_id": "taxcalc_42",
    }
