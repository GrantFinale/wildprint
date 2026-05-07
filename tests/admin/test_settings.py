"""Settings page tests — Users, API keys, Integrations."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize(
    "path",
    [
        "/admin/settings/users",
        "/admin/settings/api-keys",
        "/admin/settings/audit",
    ],
)
def test_admin_only_settings_pages_403_for_staff(
    client: FlaskClient, role_setter: Callable[[str | None], None], path: str
) -> None:
    """Users / API keys / Audit log are admin-only per the IA matrix."""
    role_setter("staff")
    resp = client.get(path)
    assert resp.status_code == 403, f"{path} should 403 for staff"


def test_users_page_lists_users_when_table_present(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Users page renders successfully (table may be empty in fresh DB)."""
    role_setter("admin")
    resp = client.get("/admin/settings/users")
    assert resp.status_code == 200
    assert b"Users" in resp.data
    # The invite form is always rendered.
    assert b"Invite" in resp.data


def test_invite_user_creates_with_random_password_and_surfaces_once(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """POSTing to /invite redirects with the temp password in the query string."""
    role_setter("admin")
    resp = client.post(
        "/admin/settings/users/invite",
        data={"email": "newuser@example.com", "role": "viewer"},
        follow_redirects=False,
    )
    # Redirect to GET /admin/settings/users?invited=...&temp_password=...
    assert resp.status_code in {302, 303}
    location = resp.headers.get("Location", "")
    # Werkzeug may or may not URL-encode @ in query strings; accept either.
    assert ("invited=newuser%40example.com" in location
            or "invited=newuser@example.com" in location)
    assert "temp_password=" in location

    # Following the redirect should render the temp password ONCE.
    follow = client.get(location)
    assert follow.status_code == 200
    body = follow.data.decode("utf-8")
    assert "newuser@example.com" in body
    # The temp password section appears with the "shown once" wording.
    assert "shown <strong>once</strong>" in body


def test_invite_rejects_invalid_email(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Invite POST with no @ in the email redirects with an error flash."""
    role_setter("admin")
    resp = client.post(
        "/admin/settings/users/invite",
        data={"email": "bad-email", "role": "viewer"},
        follow_redirects=False,
    )
    # No `invited=` param on the redirect when validation fails.
    location = resp.headers.get("Location", "")
    assert "invited=" not in location


def test_api_keys_masks_values(
    client: FlaskClient,
    role_setter: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API keys page never renders raw secrets — only masked values."""
    role_setter("admin")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_1234567890ABCDEFGH")
    resp = client.get("/admin/settings/api-keys")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # Raw value never appears.
    assert "sk_live_1234567890ABCDEFGH" not in body
    # Masked form (first 4 + last 4) should be present.
    assert "sk_l" in body
    assert "FGH" in body


def test_integrations_page_renders(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Integrations health dashboard is admin/staff."""
    role_setter("staff")
    resp = client.get("/admin/settings/integrations")
    assert resp.status_code == 200
    assert b"Integrations" in resp.data


def test_audit_log_page_shows_phase5_placeholder(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Audit log page renders the Phase 5 placeholder until the table exists."""
    role_setter("admin")
    resp = client.get("/admin/settings/audit")
    assert resp.status_code == 200
    assert b"Phase 5" in resp.data


def test_my_account_visible_to_all_roles(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """My account is the one Settings page every role can reach."""
    for role in ("admin", "staff", "viewer"):
        role_setter(role)
        resp = client.get("/admin/settings/account")
        assert resp.status_code == 200, (
            f"My account 200 expected for role={role}"
        )
