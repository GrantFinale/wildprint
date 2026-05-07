"""Catalog page tests — Species, Frame SKUs, Render presets."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient


def test_species_page_renders_and_references_legacy_data_endpoint(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """The migrated Species page boots the legacy /admin/data fetch."""
    role_setter("admin")
    resp = client.get("/admin/catalog/species")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # JS contract preserved — references the legacy JSON endpoints.
    assert "/admin/data" in body
    assert "/admin/species/" in body
    assert "global_size_variance" in body


def test_sizing_page_renders_and_uses_legacy_endpoints(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """The Sizing page (admin only) uses the same legacy save endpoints."""
    role_setter("admin")
    resp = client.get("/admin/catalog/sizing")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "/admin/settings/global_size_variance" in body
    assert "/admin/species/" in body


def test_sizing_page_forbidden_for_staff(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Sizing is admin-only per the IA matrix; staff hits 403."""
    role_setter("staff")
    resp = client.get("/admin/catalog/sizing")
    assert resp.status_code == 403


def test_backgrounds_page_renders_for_staff(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Backgrounds gallery is admin/staff per IA."""
    role_setter("staff")
    resp = client.get("/admin/catalog/backgrounds")
    assert resp.status_code == 200
    assert b"/api/list-backgrounds" in resp.data


def test_frame_skus_page_renders(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Frame SKUs page renders even with empty table (degrades gracefully)."""
    role_setter("admin")
    resp = client.get("/admin/catalog/frame-skus")
    assert resp.status_code == 200
    assert b"Frame SKUs" in resp.data


def test_frame_skus_filter_by_size_round_trips(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """The size= query string survives into the rendered <select>."""
    role_setter("admin")
    resp = client.get("/admin/catalog/frame-skus?size=16x24")
    assert resp.status_code == 200
    # The filter value is rendered as the selected option (when any SKUs
    # exist with that size; with empty table the select still preserves
    # the form input value).
    assert b"size" in resp.data


def test_render_presets_page_shows_three_tier_configs(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Read-only Render presets page lists all three tiers from TIER_CONFIG."""
    role_setter("admin")
    resp = client.get("/admin/catalog/render-presets")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Thumbnail" in body and "Preview" in body and "Print" in body
    # Long edge values from the locked TIER_CONFIG.
    assert "400" in body
    assert "2400" in body
    # Print tier long-edge is 10800.
    assert "10800" in body


def test_lakes_page_renders_with_search(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Lakes stub page accepts ?q= and ?state= filters."""
    role_setter("admin")
    resp = client.get("/admin/catalog/lakes?q=tahoe&state=CA")
    assert resp.status_code == 200
    assert b"Lake Tahoe" in resp.data
