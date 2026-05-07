"""TOTP (RFC 6238) 2FA helpers — wraps :mod:`pyotp`.

Public API:

* :func:`enroll`  — generate a fresh secret + 10 single-use recovery codes
                    and persist them on the user. Returns the secret, a
                    QR-code data URL, and the plaintext recovery codes
                    (shown to the user EXACTLY ONCE).
* :func:`verify`  — accept a 6-digit TOTP code OR a plaintext recovery
                    code. Recovery codes are consumed on use.
* :func:`disable` — clear secret + recovery codes on the user.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from review_app.auth.models import User

_RECOVERY_CODE_COUNT: int = 10
_RECOVERY_CODE_LEN: int = 10  # base32 chars; ~50 bits entropy each
_QR_ISSUER: str = "fishingposter admin"


def _new_secret() -> str:
    """Return a fresh base32-encoded TOTP secret (160 bits)."""
    import pyotp

    return pyotp.random_base32()  # type: ignore[no-any-return]


def _new_recovery_code() -> str:
    """Return one human-friendly recovery code (10 base32 chars)."""
    return base64.b32encode(secrets.token_bytes(8))[:_RECOVERY_CODE_LEN].decode(
        "ascii"
    )


def _hash_recovery(code: str) -> str:
    """SHA-256 hex digest of a normalized recovery code."""
    normalized = code.strip().upper().replace(" ", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _qr_data_url(uri: str) -> str:
    """Render the otpauth:// URI as a PNG QR code data URL.

    Falls back to a plaintext data URL if ``qrcode`` (or its PIL backend)
    isn't available — the secret remains usable via manual entry.
    """
    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        # No QR support — return the URI itself so the template can show
        # a copy-paste fallback.
        return uri

    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def enroll(user: User) -> dict[str, object]:
    """Generate + persist a new TOTP secret and recovery codes for ``user``.

    The user is mutated in place; caller is responsible for committing.
    Returns ``{"secret": str, "qr_data_url": str, "recovery_codes": list[str]}``.
    """
    import pyotp

    secret = _new_secret()
    user.totp_secret = secret
    user.totp_enrolled_at = datetime.now(UTC)

    recovery = [_new_recovery_code() for _ in range(_RECOVERY_CODE_COUNT)]
    user.totp_recovery_codes_hashed = json.dumps(
        [_hash_recovery(c) for c in recovery]
    )

    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name=_QR_ISSUER
    )
    return {
        "secret": secret,
        "qr_data_url": _qr_data_url(uri),
        "recovery_codes": recovery,
    }


def verify(user: User, code: str) -> bool:
    """Verify a 6-digit TOTP code OR a plaintext recovery code.

    Recovery codes are consumed on use (removed from the stored list).
    Returns True on success. Caller commits.
    """
    import pyotp

    if not user.totp_secret:
        return False

    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned:
        return False

    # First try the 6-digit TOTP path.
    if cleaned.isdigit() and len(cleaned) == 6:
        try:
            return bool(pyotp.totp.TOTP(user.totp_secret).verify(cleaned, valid_window=1))
        except Exception:
            return False

    # Fall back to the recovery code path.
    if not user.totp_recovery_codes_hashed:
        return False
    try:
        hashes = json.loads(user.totp_recovery_codes_hashed)
    except (TypeError, ValueError):
        return False

    target = _hash_recovery(cleaned)
    if target not in hashes:
        return False

    # Consume the code so it can't be reused.
    hashes.remove(target)
    user.totp_recovery_codes_hashed = json.dumps(hashes)
    return True


def disable(user: User) -> None:
    """Clear all 2FA state on ``user``. Caller commits."""
    user.totp_secret = None
    user.totp_enrolled_at = None
    user.totp_recovery_codes_hashed = None


def is_enrolled(user: User) -> bool:
    """Return True iff the user has completed TOTP enrollment."""
    return bool(getattr(user, "totp_secret", None))


__all__ = ["disable", "enroll", "is_enrolled", "verify"]
