"""Tests for review_app.auth.api_tokens (Phase 6 polish)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from review_app.auth import api_tokens as api_tokens_mod
from review_app.auth.api_token_models import UserApiToken
from review_app.auth.models import User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture()
def admin_user(db_session: "Session") -> User:
    user = User.create(email="api-token-test@example.com", password="x" * 12, role="admin")
    db_session.add(user)
    db_session.flush()
    return user


def test_create_returns_plaintext_and_persists_hash(
    db_session: "Session", admin_user: User
) -> None:
    """Plaintext starts with wp_; persisted row stores a hash, not plaintext."""
    payload = api_tokens_mod.create(
        admin_user, name="ci", scopes=["orders.read"], session=db_session
    )
    assert payload["plaintext"].startswith("wp_")
    row = payload["token"]
    assert row.token_hash != payload["plaintext"]
    assert json.loads(row.scopes) == ["orders.read"]


def test_create_rejects_empty_name(
    db_session: "Session", admin_user: User
) -> None:
    with pytest.raises(ValueError):
        api_tokens_mod.create(admin_user, name="", scopes=[], session=db_session)


def test_verify_resolves_user_and_marks_last_used(
    db_session: "Session", admin_user: User
) -> None:
    payload = api_tokens_mod.create(
        admin_user, name="t", scopes=[], session=db_session
    )
    db_session.flush()
    resolved = api_tokens_mod.verify(payload["plaintext"], session=db_session)
    assert resolved is not None
    assert str(resolved.id) == str(admin_user.id)

    row = db_session.get(UserApiToken, payload["token"].id)
    assert row is not None and row.last_used_at is not None


def test_verify_rejects_unknown_token(db_session: "Session") -> None:
    assert api_tokens_mod.verify("wp_doesnotexist", session=db_session) is None


def test_verify_rejects_revoked_token(
    db_session: "Session", admin_user: User
) -> None:
    payload = api_tokens_mod.create(
        admin_user, name="t", scopes=[], session=db_session
    )
    api_tokens_mod.revoke(
        str(payload["token"].id), admin_user, session=db_session
    )
    assert api_tokens_mod.verify(payload["plaintext"], session=db_session) is None


def test_verify_rejects_expired_token(
    db_session: "Session", admin_user: User
) -> None:
    payload = api_tokens_mod.create(
        admin_user,
        name="t",
        scopes=[],
        expires_in=timedelta(seconds=-1),  # already expired
        session=db_session,
    )
    db_session.flush()
    assert api_tokens_mod.verify(payload["plaintext"], session=db_session) is None


def test_revoke_only_owner_can_revoke(
    db_session: "Session", admin_user: User
) -> None:
    other = User.create(
        email="other@example.com", password="x" * 12, role="admin"
    )
    db_session.add(other)
    db_session.flush()

    payload = api_tokens_mod.create(
        admin_user, name="t", scopes=[], session=db_session
    )
    # Wrong user cannot revoke.
    assert (
        api_tokens_mod.revoke(
            str(payload["token"].id), other, session=db_session
        )
        is False
    )
    # Owner can revoke.
    assert (
        api_tokens_mod.revoke(
            str(payload["token"].id), admin_user, session=db_session
        )
        is True
    )


def test_requires_api_token_decorator_grants_access(
    db_session: "Session", admin_user: User
) -> None:
    """Endpoint with @requires_api_token('foo') accepts a token granting that scope."""
    from flask import Flask

    payload = api_tokens_mod.create(
        admin_user, name="t", scopes=["foo"], session=db_session
    )
    db_session.flush()
    db_session.commit()

    # Stamp our test session into the live module so the decorator's
    # internal verify() call hits the same DB the test uses.
    import review_app.db as _db_mod
    from sqlalchemy.orm import sessionmaker

    saved = _db_mod._session_factory
    _db_mod._session_factory = sessionmaker(
        bind=db_session.bind, autoflush=False, autocommit=False, expire_on_commit=False
    )
    try:
        app = Flask(__name__)

        @app.route("/x")
        @api_tokens_mod.requires_api_token("foo")
        def _x() -> str:
            return "ok"

        @app.route("/y")
        @api_tokens_mod.requires_api_token("bar")
        def _y() -> str:
            return "ok"

        client = app.test_client()
        resp = client.get(
            "/x", headers={"Authorization": f"Bearer {payload['plaintext']}"}
        )
        assert resp.status_code == 200

        # No header -> 401.
        resp_anon = client.get("/x")
        assert resp_anon.status_code == 401

        # Wrong scope -> 403.
        resp_scope = client.get(
            "/y", headers={"Authorization": f"Bearer {payload['plaintext']}"}
        )
        assert resp_scope.status_code == 403
    finally:
        _db_mod._session_factory = saved
