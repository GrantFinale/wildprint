"""Tests for review_app.auth.totp (Phase 6 polish)."""
from __future__ import annotations

import json

import pytest

from review_app.auth import totp as totp_mod


class _StubUser:
    """In-memory User stand-in (no DB session needed for unit-level totp tests)."""

    def __init__(self, email: str = "stub@example.com") -> None:
        self.email = email
        self.totp_secret: str | None = None
        self.totp_enrolled_at = None
        self.totp_recovery_codes_hashed: str | None = None


def test_enroll_sets_secret_qr_and_recovery() -> None:
    """enroll() returns secret, QR data url, and 10 recovery codes."""
    user = _StubUser()
    payload = totp_mod.enroll(user)
    assert isinstance(payload["secret"], str) and payload["secret"]
    assert isinstance(payload["qr_data_url"], str)
    codes = payload["recovery_codes"]
    assert isinstance(codes, list) and len(codes) == 10
    assert user.totp_secret == payload["secret"]
    assert user.totp_recovery_codes_hashed
    stored = json.loads(user.totp_recovery_codes_hashed)
    assert len(stored) == 10


def test_verify_accepts_current_totp_code() -> None:
    """The current TOTP code (from pyotp.now()) verifies."""
    import pyotp

    user = _StubUser()
    totp_mod.enroll(user)
    code = pyotp.totp.TOTP(user.totp_secret).now()  # type: ignore[arg-type]
    assert totp_mod.verify(user, code) is True


def test_verify_rejects_wrong_totp_code() -> None:
    """A 6-digit code that isn't valid is rejected."""
    user = _StubUser()
    totp_mod.enroll(user)
    assert totp_mod.verify(user, "000000") is False


def test_verify_consumes_recovery_code() -> None:
    """A recovery code works once and is then removed from the stored list."""
    user = _StubUser()
    payload = totp_mod.enroll(user)
    code = payload["recovery_codes"][0]  # type: ignore[index]
    assert totp_mod.verify(user, str(code)) is True
    # Second use of the same code must fail.
    assert totp_mod.verify(user, str(code)) is False
    # Stored list shrunk by one.
    stored = json.loads(user.totp_recovery_codes_hashed)
    assert len(stored) == 9


def test_verify_returns_false_without_secret() -> None:
    """A user who hasn't enrolled cannot verify anything."""
    user = _StubUser()
    assert totp_mod.verify(user, "123456") is False


def test_disable_clears_state() -> None:
    """disable() wipes secret + recovery codes."""
    user = _StubUser()
    totp_mod.enroll(user)
    totp_mod.disable(user)
    assert user.totp_secret is None
    assert user.totp_recovery_codes_hashed is None
    assert user.totp_enrolled_at is None


def test_is_enrolled_reflects_secret_presence() -> None:
    user = _StubUser()
    assert totp_mod.is_enrolled(user) is False
    totp_mod.enroll(user)
    assert totp_mod.is_enrolled(user) is True
    totp_mod.disable(user)
    assert totp_mod.is_enrolled(user) is False
