"""Magic-link authentication helpers for /account/*.

The flow:

1. Customer enters their email at ``GET /account/login`` and submits.
2. We look up the customer (auto-create one if none exists with that email
   AND the email is well-formed — keeps friction low for first-time buyers).
3. Generate a JWT with claims ``{"sub": customer_id, "exp": ts, "jti": ...}``
   and store its SHA-256 hash in ``customer_login_tokens``. Email the link.
4. ``GET /account/login/verify?token=<jwt>`` decodes the JWT, checks the row
   is unused and unexpired, marks ``used_at = now()``, sets
   ``session['customer_id']``, redirects to ``next`` (or /account/).

JWT secret is the Flask ``SECRET_KEY``. Tokens live 15 minutes.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import jwt as _jwt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

TOKEN_TTL = timedelta(minutes=15)
JWT_ALGORITHM = "HS256"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(
    session: Session,
    *,
    customer_id: uuid.UUID,
    secret_key: str,
    ip_address: str | None = None,
) -> str:
    """Create a JWT + persist its hash. Returns the JWT (the magic link payload)."""
    from review_app.account.models import CustomerLoginToken

    now = datetime.now(UTC)
    expires_at = now + TOKEN_TTL
    jti = secrets.token_urlsafe(16)
    claims = {
        "sub": str(customer_id),
        "exp": int(expires_at.timestamp()),
        "iat": int(now.timestamp()),
        "jti": jti,
    }
    token = _jwt.encode(claims, secret_key, algorithm=JWT_ALGORITHM)

    row = CustomerLoginToken(
        customer_id=customer_id,
        token_hash=_hash_token(token),
        issued_at=now,
        expires_at=expires_at,
        ip_address=ip_address,
    )
    session.add(row)
    session.flush()
    return token


def verify_token(
    session: Session, *, token: str, secret_key: str
) -> uuid.UUID | None:
    """Validate a magic link token. Returns the customer_id on success.

    Marks the token row as used. Returns ``None`` for: invalid signature,
    expired exp claim, missing/used DB row.
    """
    from review_app.account.models import CustomerLoginToken

    try:
        claims = _jwt.decode(token, secret_key, algorithms=[JWT_ALGORITHM])
    except (_jwt.PyJWTError, ValueError):
        return None

    sub = claims.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        customer_id = uuid.UUID(sub)
    except ValueError:
        return None

    th = _hash_token(token)
    from sqlalchemy import select

    row = session.execute(
        select(CustomerLoginToken).where(CustomerLoginToken.token_hash == th)
    ).scalar_one_or_none()
    if row is None or row.used_at is not None:
        return None
    now = datetime.now(UTC)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        return None

    row.used_at = now
    session.flush()
    return customer_id


def send_magic_link(
    session: Session,
    *,
    customer_email: str,
    token: str,
    base_url: str,
) -> None:
    """Enqueue an outbox row delivering the magic-link email."""
    from review_app.email.outbox import enqueue

    link = f"{base_url.rstrip('/')}/account/login/verify?token={token}"
    enqueue(
        session,
        kind="email.account_magic_link",
        to=customer_email,
        payload={
            "subject": "Sign in to your fishingposter.com account",
            "body": (
                "Click the link below to sign in. It expires in 15 minutes.\n\n"
                f"{link}\n\n"
                "If you didn't request this, you can ignore the email."
            ),
            "magic_link": link,
        },
    )


__all__ = ["TOKEN_TTL", "issue_token", "send_magic_link", "verify_token"]
