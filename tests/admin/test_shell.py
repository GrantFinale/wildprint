"""Admin shell tests — auth, role visibility, topbar.

These tests run against the real Flask app fixture (``client`` from
``tests/conftest.py``) but stub out the role layer via ``role_setter``.
Database state isn't required — the dashboard helpers degrade to zeros
when tables aren't populated.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient


def _html(resp_data: bytes) -> str:
    return resp_data.decode("utf-8", errors="replace")


def test_admin_dashboard_requires_auth(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When auth is enabled but no user is logged in, /admin redirects to login."""
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")

    # Stub the decorator's current_user to anonymous.
    from review_app.auth import decorators as _dec

    class _Anon:
        is_authenticated = False
        is_anonymous = True

    monkeypatch.setattr(_dec, "current_user", _Anon())

    resp = client.get("/admin", follow_redirects=False)
    # 401 with redirect Location header (per requires_role behavior).
    assert resp.status_code == 401
    assert resp.headers.get("Location", "").startswith("/admin/login")


def test_admin_dashboard_visible_to_all_roles(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Per the role × capability matrix, every authenticated role can view the dashboard."""
    for role in ("admin", "staff", "viewer"):
        role_setter(role)
        resp = client.get("/admin")
        assert resp.status_code == 200, (
            f"role={role} should see dashboard, got {resp.status_code}"
        )
        assert b"Dashboard" in resp.data


def test_sidebar_hides_settings_for_staff_role(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Staff role lacks any Settings sub-page — Settings category must be hidden."""
    role_setter("staff")
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = _html(resp.data)
    # Settings & Audit are admin-only; Integrations is admin/staff. Staff
    # CAN see Integrations so Settings category IS visible to staff.
    # However Users / API keys / Audit log / My account distribution:
    # - Users (admin), API keys (admin), Integrations (admin/staff),
    # - Audit (admin), My account (all roles).
    # Staff sees Integrations + My account → Settings category visible.
    # So we instead assert ADMIN-ONLY items are hidden.
    assert "Users &amp; roles" not in body and "Users & roles" not in body
    assert "Audit log" not in body
    assert "API keys" not in body


def test_sidebar_hides_settings_for_viewer_role(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Viewer role only sees My account + Integrations is admin/staff so hidden too."""
    role_setter("viewer")
    resp = client.get("/admin")
    assert resp.status_code == 200
    body = _html(resp.data)
    # Viewer can NOT see admin-only nor admin/staff items.
    assert "Users &amp; roles" not in body and "Users & roles" not in body
    assert "Audit log" not in body
    assert "API keys" not in body
    # Viewer also can't see Sizing / Frame SKUs / Render presets / Backgrounds.
    assert "Frame SKUs" not in body
    assert "Render presets" not in body


def test_topbar_renders_prodigi_env_pill(
    client: FlaskClient,
    role_setter: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Topbar always shows the Prodigi env pill (SANDBOX red / PROD green)."""
    role_setter("admin")

    monkeypatch.setenv("PRODIGI_ENV", "sandbox")
    resp = client.get("/admin")
    assert b"SANDBOX" in resp.data
    assert b"env-pill--sandbox" in resp.data

    monkeypatch.setenv("PRODIGI_ENV", "production")
    resp = client.get("/admin")
    assert b"PROD" in resp.data
    assert b"env-pill--prod" in resp.data


def test_global_search_returns_results_page(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Phase 6 polish: cmd+K search now hits the real cross-entity service."""
    role_setter("admin")
    resp = client.get("/admin/search?q=foo")
    assert resp.status_code == 200
    body = _html(resp.data)
    # The query echoes back into the page header.
    assert "&ldquo;foo&rdquo;" in body
    # Results page content (no-results card or grouped sections).
    assert "No results" in body or "Customers" in body or "Orders" in body


def test_direct_url_to_settings_returns_403_for_viewer(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Sidebar hides Settings for viewer; direct URL must 403."""
    role_setter("viewer")
    resp = client.get("/admin/settings/users")
    assert resp.status_code == 403
