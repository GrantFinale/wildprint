"""Per-user API token service (Phase 6 polish).

Tokens are issued in the format ``wp_<base64url>``. The plaintext is shown
to the user exactly once at creation time; only its SHA-256 hex digest is
persisted in the ``user_api_tokens`` table.

Public API:

* :func:`create`  — issue a new token, return ``{"token": row, "plaintext": str}``.
* :func:`verify`  — look up a token by plaintext, mark ``last_used_at``.
* :func:`revoke`  — mark a token revoked.
* :func:`requires_api_token` — Flask decorator parallel to ``requires_role``.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from flask import Response, request
from sqlalchemy import select

from review_app.auth.api_token_models import UserApiToken
from review_app.db import get_session

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from review_app.auth.models import User

F = TypeVar("F", bound=Callable[..., Any])

_PREFIX: str = "wp_"
# 30 random bytes -> 40-char base64-url; with prefix the full token is 43 chars.
_PLAINTEXT_BYTES: int = 30


def _hash(plaintext: str) -> str:
    """SHA-256 hex digest of the supplied plaintext."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _new_plaintext() -> str:
    """Return a fresh ``wp_<40-char-b64url>`` token plaintext."""
    body = base64.urlsafe_b64encode(secrets.token_bytes(_PLAINTEXT_BYTES))
    return _PREFIX + body.decode("ascii").rstrip("=")[:40]


def create(
    user: User,
    *,
    name: str,
    scopes: list[str],
    expires_in: timedelta | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Issue a new API token for ``user`` and persist its hash.

    The returned dict has two keys:
        - ``"token"``: the persisted :class:`UserApiToken` row.
        - ``"plaintext"``: the one-time plaintext string. SHOW ONCE.

    Raises :class:`ValueError` for an empty name. ``expires_in`` is optional.
    """
    if not (name or "").strip():
        raise ValueError("name must not be empty")

    plaintext = _new_plaintext()
    token_hash = _hash(plaintext)
    expires_at = (
        datetime.now(UTC) + expires_in if expires_in is not None else None
    )

    row = UserApiToken(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        name=name.strip()[:200],
        token_hash=token_hash,
        scopes=json.dumps(list(scopes or [])),
        expires_at=expires_at,
    )
    if session is None:
        with get_session() as s:
            s.add(row)
            s.flush()
    else:
        session.add(row)
        session.flush()
    return {"token": row, "plaintext": plaintext}


def verify(plaintext: str, *, session: Any = None) -> User | None:
    """Resolve a plaintext token to its owning :class:`User`.

    Updates ``last_used_at`` on success. Returns None for unknown tokens,
    revoked tokens, or expired tokens.
    """
    from review_app.auth.models import User

    if not plaintext or not plaintext.startswith(_PREFIX):
        return None
    token_hash = _hash(plaintext)
    now = datetime.now(UTC)

    def _do(s: Any) -> User | None:
        row = s.execute(
            select(UserApiToken).where(UserApiToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at < now:
            return None
        row.last_used_at = now
        s.flush()
        owner = User.get_active_by_id(s, row.user_id)
        return owner

    if session is None:
        with get_session() as s:
            return _do(s)
    return _do(session)


def revoke(token_id: str, user: User, *, session: Any = None) -> bool:
    """Mark a token as revoked. Returns True if a row was updated.

    The user must own the token; cross-user revocations silently fail.
    """
    def _do(s: Any) -> bool:
        row = s.get(UserApiToken, str(token_id))
        if row is None or str(row.user_id) != str(user.id):
            return False
        if row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(UTC)
        s.flush()
        return True

    if session is None:
        with get_session() as s:
            return _do(s)
    return _do(session)


def requires_api_token(*scopes: str) -> Callable[[F], F]:
    """Gate a Flask view on a valid Bearer token with all listed scopes.

    Usage::

        @app.route("/api/posters")
        @requires_api_token("posters.read")
        def list_posters():
            ...

    Returns 401 for missing/invalid tokens, 403 for insufficient scopes.
    On success, the resolved User is attached to ``flask.g.api_user`` and
    the row's scopes are at ``flask.g.api_token_scopes``.
    """
    def decorator(view: F) -> F:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from flask import g

            authz = request.headers.get("Authorization", "")
            if not authz.lower().startswith("bearer "):
                return Response(
                    '{"error": "missing bearer token"}',
                    status=401,
                    mimetype="application/json",
                )
            plaintext = authz[7:].strip()
            owner = verify(plaintext)
            if owner is None:
                return Response(
                    '{"error": "invalid or expired token"}',
                    status=401,
                    mimetype="application/json",
                )

            # Scope check.
            if scopes:
                # Reload token row to inspect scopes.
                with get_session() as s:
                    row = s.execute(
                        select(UserApiToken).where(
                            UserApiToken.token_hash == _hash(plaintext)
                        )
                    ).scalar_one_or_none()
                    granted = set()
                    if row is not None and row.scopes:
                        try:
                            granted = set(json.loads(row.scopes))
                        except (TypeError, ValueError):
                            granted = set()
                if not set(scopes).issubset(granted):
                    return Response(
                        '{"error": "insufficient scope"}',
                        status=403,
                        mimetype="application/json",
                    )
                g.api_token_scopes = sorted(granted)
            else:
                g.api_token_scopes = []

            g.api_user = owner
            return view(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["create", "requires_api_token", "revoke", "verify"]
