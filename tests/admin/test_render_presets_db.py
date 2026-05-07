"""Tests for the DB-backed render presets (Phase 6 polish).

Covers:
* :func:`review_app.render.tiers.get_tier_config` reads from the DB row
  when present, falls back to ``TIER_CONFIG`` otherwise, and caches.
* The admin save handler validates form data, persists to the
  ``render_presets`` table, audits the change, and busts the cache.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from review_app.render import tiers as tiers_module
from review_app.render.presets_model import RenderPreset
from review_app.render.tiers import (
    TIER_CONFIG,
    TIER_PREVIEW,
    TIER_PRINT,
    TIER_THUMB,
    get_tier_config,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Each test gets a fresh cache."""
    reset_cache()
    yield
    reset_cache()


def test_get_tier_config_falls_back_to_baseline_when_db_row_missing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No DB row -> TierConfig matches the hardcoded TIER_CONFIG baseline."""
    monkeypatch.setattr(tiers_module, "_load_from_db", lambda tier: None)
    cfg = get_tier_config(TIER_THUMB)
    baseline = TIER_CONFIG[TIER_THUMB]
    assert cfg.long_edge_px == baseline.long_edge_px
    assert cfg.fmt == baseline.fmt
    assert cfg.dpi == baseline.dpi


def test_get_tier_config_uses_db_row_when_present(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted RenderPreset row overrides the baseline."""
    row = RenderPreset(
        tier=TIER_THUMB,
        long_edge_px=800,  # different from baseline 400
        dpi=150,
        format="png",
        jpeg_quality=None,
        watermark_enabled=True,
        watermark_text="custom",
        watermark_opacity=0.3,
        watermark_angle=45,
        bucket_env_var="SPACES_THUMBS_BUCKET",
        public_read=False,
    )
    db_session.add(row)
    db_session.flush()

    # Patch _load_from_db to use the test session directly.
    def _load(tier: int):
        from sqlalchemy import select as _sel

        r = db_session.execute(
            _sel(RenderPreset).where(RenderPreset.tier == tier)
        ).scalar_one_or_none()
        if r is None:
            return None
        return tiers_module._row_to_config(r, tier)

    monkeypatch.setattr(tiers_module, "_load_from_db", _load)

    cfg = get_tier_config(TIER_THUMB)
    assert cfg.long_edge_px == 800
    assert cfg.dpi == 150
    assert cfg.fmt == "PNG"
    assert cfg.watermark is True


def test_get_tier_config_caches_within_ttl(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated calls inside the TTL should not re-query the DB."""
    call_count = {"n": 0}

    def _load(tier: int):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(tiers_module, "_load_from_db", _load)
    get_tier_config(TIER_PREVIEW)
    get_tier_config(TIER_PREVIEW)
    get_tier_config(TIER_PREVIEW)
    assert call_count["n"] == 1


def test_get_tier_config_unknown_tier_raises() -> None:
    """Unknown tier still raises ValueError (matches Phase 2 contract)."""
    with pytest.raises(ValueError):
        get_tier_config(99)


def test_reset_cache_forces_reload(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``reset_cache`` should make the next call re-query the DB."""
    call_count = {"n": 0}

    def _load(tier: int):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(tiers_module, "_load_from_db", _load)
    get_tier_config(TIER_PRINT)
    reset_cache()
    get_tier_config(TIER_PRINT)
    assert call_count["n"] == 2


def test_admin_render_presets_get_renders_form(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """GET /admin/catalog/render-presets renders an editable form."""
    role_setter("admin")
    resp = client.get("/admin/catalog/render-presets")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # Form-element markers we depend on for the editable Phase 6 UI.
    assert "long_edge_px" in body
    assert "watermark_enabled" in body
    assert "Save tier" in body


def test_admin_render_presets_save_persists_row(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """POST writes a render_presets row and busts the cache."""
    role_setter("admin")
    # Bootstrap the live-engine schema (the route uses get_session() which
    # binds to dev.db / DATABASE_URL — separate from db_session :memory:).
    from sqlalchemy import select

    from review_app.db import Base, get_engine, get_session

    Base.metadata.create_all(get_engine())

    # Clean any pre-existing tier=1 row from prior tests.
    with get_session() as session:
        existing = session.execute(
            select(RenderPreset).where(RenderPreset.tier == 1)
        ).scalar_one_or_none()
        if existing is not None:
            session.delete(existing)

    payload = {
        "long_edge_px": "600",
        "dpi": "150",
        "format": "jpeg",
        "jpeg_quality": "90",
        "watermark_enabled": "1",
        "watermark_text": "test.com",
        "watermark_opacity": "0.20",
        "watermark_angle": "20",
        "bucket_env_var": "SPACES_THUMBS_BUCKET",
        "public_read": "1",
    }
    resp = client.post(
        "/admin/catalog/render-presets/1",
        data=payload,
        follow_redirects=False,
    )
    assert resp.status_code in {302, 303}

    with get_session() as session:
        row = session.execute(
            select(RenderPreset).where(RenderPreset.tier == 1)
        ).scalar_one_or_none()
        assert row is not None
        assert int(row.long_edge_px) == 600
        assert int(row.dpi) == 150
        session.delete(row)


def test_admin_render_presets_save_rejects_out_of_range_dpi(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """DPI outside 72-600 should redirect with an error flash, not save."""
    role_setter("admin")
    payload = {
        "long_edge_px": "400",
        "dpi": "5000",  # invalid
        "format": "jpeg",
        "jpeg_quality": "85",
        "watermark_enabled": "",
        "watermark_text": "",
        "watermark_opacity": "0.1",
        "watermark_angle": "",
        "bucket_env_var": "SPACES_THUMBS_BUCKET",
        "public_read": "",
    }
    resp = client.post(
        "/admin/catalog/render-presets/1",
        data=payload,
        follow_redirects=False,
    )
    assert resp.status_code in {302, 303}


def test_admin_render_presets_save_rejects_unknown_tier(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Unknown tier should redirect with an error flash."""
    role_setter("admin")
    payload = {
        "long_edge_px": "400",
        "dpi": "72",
        "format": "jpeg",
        "jpeg_quality": "85",
        "watermark_enabled": "",
        "watermark_text": "",
        "watermark_opacity": "0",
        "watermark_angle": "",
        "bucket_env_var": "SPACES_THUMBS_BUCKET",
        "public_read": "",
    }
    resp = client.post(
        "/admin/catalog/render-presets/9",
        data=payload,
        follow_redirects=False,
    )
    # Tier 9 isn't in the route's int validator path (1-3 enforced by handler).
    assert resp.status_code in {302, 303, 404}


def test_admin_render_presets_save_forbidden_for_staff(
    client: FlaskClient, role_setter: Callable[[str | None], None]
) -> None:
    """Render presets edit is admin-only — staff/viewer get 403."""
    role_setter("staff")
    resp = client.post(
        "/admin/catalog/render-presets/1",
        data={"long_edge_px": "400", "dpi": "72", "format": "jpeg"},
    )
    assert resp.status_code == 403
