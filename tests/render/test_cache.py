"""Tests for the cache-aware tier dispatcher."""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from review_app.db.base import Base
from review_app.render.cache import TierResult, get_or_render_tier
from review_app.render.db_models import RenderOutputRow, RenderSpecRow
from review_app.render.spec import RenderSpec
from review_app.render.tiers import (
    PRINT_CANVAS_HEIGHT,
    PRINT_CANVAS_WIDTH,
    TIER_PREVIEW,
    TIER_PRINT,
    TIER_THUMB,
)


# ---------------------------------------------------------------------------
# In-memory sqlite session fixture (isolated per test)
# ---------------------------------------------------------------------------
@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def spec() -> RenderSpec:
    return RenderSpec(
        lake="Test Lake",
        species=["bass"],
        art_style="editorial-v1",
        layout_config={"grid": 1},
    )


@pytest.fixture
def fake_master_renderer():
    """Tiny deterministic master to keep the synchronous render fast."""

    def _factory(spec: RenderSpec, w: int, h: int) -> Image.Image:
        arr = np.full((h, w, 3), 80, dtype=np.uint8)
        arr[:, : w // 2, 0] = 200
        return Image.fromarray(arr, mode="RGB")

    return _factory


@pytest.fixture(autouse=True)
def _set_endpoint_env() -> Iterator[None]:
    """``SPACES_ENDPOINT`` is read by ``_public_url`` for URL construction."""
    old = os.environ.get("SPACES_ENDPOINT")
    os.environ["SPACES_ENDPOINT"] = "https://nyc3.digitaloceanspaces.com"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SPACES_ENDPOINT", None)
        else:
            os.environ["SPACES_ENDPOINT"] = old


# ---------------------------------------------------------------------------
# Tier 1/2 — synchronous path
# ---------------------------------------------------------------------------
def test_get_or_render_tier_cache_miss_renders_and_inserts_row(
    session: Session, spec: RenderSpec, fake_master_renderer: Any
) -> None:
    upload_calls: list[dict[str, Any]] = []

    def _fake_put(**kwargs: Any) -> str:
        upload_calls.append(kwargs)
        return f"https://fake/{kwargs['key']}"

    with patch("review_app.storage.put_object", side_effect=_fake_put):
        result = get_or_render_tier(
            session,
            spec,
            TIER_THUMB,
            master_renderer=fake_master_renderer,
        )

    assert isinstance(result, TierResult)
    assert result.cached is False
    assert result.url is not None
    assert result.url.startswith("https://")
    assert len(upload_calls) == 1

    # DB rows persisted
    spec_row = session.execute(
        select(RenderSpecRow).where(RenderSpecRow.spec_hash == spec.canonical_hash())
    ).scalar_one()
    assert spec_row.renderer_version == "v1"
    out_rows = session.execute(
        select(RenderOutputRow).where(
            RenderOutputRow.render_spec_id == spec_row.id,
            RenderOutputRow.tier == TIER_THUMB,
        )
    ).scalars().all()
    assert len(out_rows) == 1
    assert out_rows[0].file_size_bytes is not None
    assert out_rows[0].file_size_bytes > 0
    assert out_rows[0].content_hash is not None


def test_get_or_render_tier_cache_hit_skips_render(
    session: Session, spec: RenderSpec, fake_master_renderer: Any
) -> None:
    """Second call for the same (spec, tier) must NOT invoke renderer or upload."""

    def _fake_put(**kwargs: Any) -> str:
        return f"https://fake/{kwargs['key']}"

    # First call — populate cache.
    with patch("review_app.storage.put_object", side_effect=_fake_put):
        first = get_or_render_tier(
            session, spec, TIER_THUMB, master_renderer=fake_master_renderer
        )
    assert first.cached is False

    # Second call — must hit cache. Track calls; renderer should NOT be invoked.
    render_calls: list[Any] = []

    def _spy_renderer(s: RenderSpec, w: int, h: int) -> Image.Image:
        render_calls.append((s, w, h))
        return fake_master_renderer(s, w, h)

    with patch("review_app.storage.put_object", side_effect=_fake_put) as put_mock:
        second = get_or_render_tier(
            session, spec, TIER_THUMB, master_renderer=_spy_renderer
        )

    assert second.cached is True
    assert second.url is not None
    assert len(render_calls) == 0, "cache hit should not re-invoke renderer"
    assert put_mock.call_count == 0, "cache hit should not re-upload"


def test_get_or_render_tier_preview_uses_preview_bucket(
    session: Session, spec: RenderSpec, fake_master_renderer: Any
) -> None:
    upload_calls: list[dict[str, Any]] = []

    def _fake_put(**kwargs: Any) -> str:
        upload_calls.append(kwargs)
        return f"https://fake/{kwargs['key']}"

    with patch("review_app.storage.put_object", side_effect=_fake_put):
        result = get_or_render_tier(
            session, spec, TIER_PREVIEW, master_renderer=fake_master_renderer
        )

    assert result.bucket == "fishingposter-previews"
    assert "previews/" in upload_calls[0]["key"]


# ---------------------------------------------------------------------------
# Tier 3 — enqueue path
# ---------------------------------------------------------------------------
def test_get_or_render_tier_tier_3_enqueues_instead_of_renders(
    session: Session, spec: RenderSpec, fake_master_renderer: Any
) -> None:
    """Tier 3 cache miss must enqueue an RQ job, NOT render synchronously."""

    class _FakeJob:
        id = "job_abc123"

    enqueue_calls: list[dict[str, Any]] = []

    def _fake_enqueue(func, *args: Any, **kwargs: Any) -> Any:
        enqueue_calls.append({"func": func, "args": args, "kwargs": kwargs})
        return _FakeJob()

    render_calls: list[Any] = []

    def _spy_renderer(s: RenderSpec, w: int, h: int) -> Image.Image:
        render_calls.append((s, w, h))
        return fake_master_renderer(s, w, h)

    with patch("review_app.queue.enqueue", side_effect=_fake_enqueue):
        result = get_or_render_tier(
            session,
            spec,
            TIER_PRINT,
            master_renderer=_spy_renderer,
            order_id="ord_test_001",
        )

    assert result.pending is True
    assert result.url is None
    assert result.job_id == "job_abc123"
    assert len(render_calls) == 0, "tier-3 must NOT render synchronously"
    assert len(enqueue_calls) == 1
    enq = enqueue_calls[0]
    # First positional arg to enqueue is the job function; then spec_dict, tier, order_id
    args = enq["args"]
    assert args[1] == TIER_PRINT
    assert args[2] == "ord_test_001"


def test_tier_3_cache_hit_returns_signed_url_without_enqueue(
    session: Session, spec: RenderSpec
) -> None:
    """A pre-existing tier-3 row must short-circuit to a signed URL."""
    spec_hash = spec.canonical_hash()
    spec_row = RenderSpecRow(
        spec_hash=spec_hash,
        canonical_inputs=spec.canonical_dict(),
        renderer_version=spec.renderer_version,
    )
    session.add(spec_row)
    session.flush()

    out_row = RenderOutputRow(
        render_spec_id=spec_row.id,
        tier=TIER_PRINT,
        storage_bucket="fishingposter-posters",
        storage_key=f"prints/ord_test_001/{spec_hash}.png",
        file_size_bytes=12345,
        content_hash="deadbeef",
    )
    session.add(out_row)
    session.flush()

    enqueue_calls: list[Any] = []

    def _fake_enqueue(*args: Any, **kwargs: Any) -> Any:
        enqueue_calls.append((args, kwargs))
        raise RuntimeError("must not enqueue on cache hit")

    def _fake_signed_url(**kwargs: Any) -> str:
        return f"https://signed/{kwargs['key']}?sig=xyz"

    with patch("review_app.queue.enqueue", side_effect=_fake_enqueue):
        with patch("review_app.storage.get_signed_url", side_effect=_fake_signed_url):
            result = get_or_render_tier(
                session,
                spec,
                TIER_PRINT,
                order_id="ord_test_001",
            )

    assert result.cached is True
    assert result.pending is False
    assert result.url is not None
    assert "signed" in result.url
    assert len(enqueue_calls) == 0
