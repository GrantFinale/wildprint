"""Catalog routes — register on the shared ``admin_bp``.

Six pages (per ``docs/admin-ia.md`` §1):

* ``/admin/catalog/species``        — species table (legacy /admin migrated here)
* ``/admin/catalog/backgrounds``    — background gallery + Flux Pro Ultra
* ``/admin/catalog/sizing``         — global variance + per-species scale
* ``/admin/catalog/frame-skus``     — 32 launch SKUs from prodigi_skus
* ``/admin/catalog/lakes``          — lake dictionary (Phase 5 → GNIS)
* ``/admin/catalog/render-presets`` — three-tier render config (read-only)

The legacy ``/admin/data``, ``/admin/species/<slug>/scale``, and
``/admin/settings/global_size_variance`` JSON endpoints are NOT moved —
they keep their original URLs so the existing JS continues to work
unchanged. The migration is purely chrome (new shell wraps the same
content + scripts).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from flask import render_template, request
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from review_app.admin._helpers import crumbs
from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role
from review_app.db import get_session
from review_app.render.tiers import TIER_CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalog / Species
# ---------------------------------------------------------------------------
@admin_bp.route("/catalog/species", methods=["GET"])
@requires_role("admin", "staff", "viewer")
def catalog_species() -> ResponseReturnValue:
    """Species catalog — preserves the legacy /admin behavior in the new shell.

    The page boots the same JS that the legacy template did. It hits the
    existing legacy ``/admin/data`` JSON endpoint (kept in ``app.py``)
    plus the per-species scale + global-variance POST endpoints. No
    behavior change — only the chrome around it.
    """
    return render_template(
        "admin/catalog/species.html",
        page_title="Species",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Species", None),
        ),
    )


# ---------------------------------------------------------------------------
# Catalog / Backgrounds
# ---------------------------------------------------------------------------
@admin_bp.route("/catalog/backgrounds", methods=["GET"])
@requires_role("admin", "staff")
def catalog_backgrounds() -> ResponseReturnValue:
    """Background gallery + Flux Pro Ultra generator.

    Uses the same legacy ``/api/background-presets``, ``/api/list-backgrounds``,
    and ``/api/generate-background`` endpoints as before — just rendered
    inside the new admin shell.
    """
    return render_template(
        "admin/catalog/backgrounds.html",
        page_title="Backgrounds",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Backgrounds", None),
        ),
    )


# ---------------------------------------------------------------------------
# Catalog / Sizing
# ---------------------------------------------------------------------------
@admin_bp.route("/catalog/sizing", methods=["GET"])
@requires_role("admin")
def catalog_sizing() -> ResponseReturnValue:
    """Global size variance + per-species scale table.

    Reuses the legacy ``/admin/data`` JSON for the per-species table and
    ``/admin/settings/global_size_variance`` POST for the slider.
    """
    return render_template(
        "admin/catalog/sizing.html",
        page_title="Sizing",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Sizing", None),
        ),
    )


# ---------------------------------------------------------------------------
# Catalog / Frame SKUs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrameSkuRow:
    """One row in the Frame SKUs table — denormalized + dollar formatting."""

    internal_sku: str
    prodigi_sku: str
    size_inches: str
    finish: str
    wholesale_cents: int | None
    retail_cents: int | None
    margin_pct: float | None
    last_refreshed_at: str
    active: bool
    in_stock: bool

    def wholesale_dollars(self) -> str:
        return _cents_to_dollars(self.wholesale_cents)

    def retail_dollars(self) -> str:
        return _cents_to_dollars(self.retail_cents)


def _cents_to_dollars(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:,.2f}"


def _margin_pct(retail: int | None, wholesale: int | None) -> float | None:
    """Margin % = (retail - wholesale) / retail * 100. None if unknown."""
    if retail is None or wholesale is None or retail == 0:
        return None
    return round((retail - wholesale) / retail * 100, 1)


@admin_bp.route("/catalog/frame-skus", methods=["GET"])
@requires_role("admin")
def catalog_frame_skus() -> ResponseReturnValue:
    """List the launch SKUs from ``prodigi_skus`` with pricing + margin."""
    size_filter = (request.args.get("size") or "").strip()
    finish_filter = (request.args.get("finish") or "").strip()
    active_filter = request.args.get("active")  # None | "true" | "false"
    low_margin_only = request.args.get("low_margin") == "1"

    rows: list[FrameSkuRow] = []
    sizes: list[str] = []
    finishes: list[str] = []

    try:
        from review_app.prodigi.db_models import ProdigiSku

        with get_session() as session:
            stmt = select(ProdigiSku).order_by(
                ProdigiSku.size_inches, ProdigiSku.finish
            )
            try:
                skus = session.execute(stmt).scalars().all()
            except (OperationalError, ProgrammingError):
                skus = []

            sizes = sorted({s.size_inches for s in skus})
            finishes = sorted({s.finish for s in skus})

            for sku in skus:
                if size_filter and sku.size_inches != size_filter:
                    continue
                if finish_filter and sku.finish != finish_filter:
                    continue
                if active_filter == "true" and not sku.active:
                    continue
                if active_filter == "false" and sku.active:
                    continue
                margin = _margin_pct(
                    sku.retail_price_cents, sku.last_quoted_wholesale_cents
                )
                if low_margin_only and (margin is None or margin >= 60):
                    continue
                rows.append(
                    FrameSkuRow(
                        internal_sku=sku.internal_sku,
                        prodigi_sku=sku.prodigi_sku,
                        size_inches=sku.size_inches,
                        finish=sku.finish,
                        wholesale_cents=sku.last_quoted_wholesale_cents,
                        retail_cents=sku.retail_price_cents,
                        margin_pct=margin,
                        last_refreshed_at=(
                            sku.last_refreshed_at.strftime("%Y-%m-%d %H:%M UTC")
                            if sku.last_refreshed_at
                            else "never"
                        ),
                        active=bool(sku.active),
                        in_stock=bool(sku.in_stock),
                    )
                )
    except ImportError:
        logger.debug("prodigi.db_models unavailable — Frame SKUs page empty")

    return render_template(
        "admin/catalog/frame_skus.html",
        page_title="Frame SKUs",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Frame SKUs", None),
        ),
        rows=rows,
        sizes=sizes,
        finishes=finishes,
        size_filter=size_filter,
        finish_filter=finish_filter,
        active_filter=active_filter or "",
        low_margin_only=low_margin_only,
    )


# ---------------------------------------------------------------------------
# Catalog / Lakes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LakeRow:
    """Stub row for the Lakes table (Phase 5 wires to GNIS dataset)."""

    name: str
    state: str
    aliases: str = ""
    order_count: int = 0


# A few well-known lakes so the page renders something instead of an empty
# table during Phase 4. Phase 5 swaps this for the GNIS-backed dictionary.
_STUB_LAKES: tuple[LakeRow, ...] = (
    LakeRow("Lake Tahoe", "CA/NV"),
    LakeRow("Lake Michigan", "MI/IL/IN/WI"),
    LakeRow("Great Salt Lake", "UT"),
    LakeRow("Lake Powell", "AZ/UT"),
    LakeRow("Lake Champlain", "VT/NY"),
    LakeRow("Lake Okeechobee", "FL"),
    LakeRow("Lake of the Woods", "MN"),
    LakeRow("Crater Lake", "OR"),
)


@admin_bp.route("/catalog/lakes", methods=["GET"])
@requires_role("admin", "staff")
def catalog_lakes() -> ResponseReturnValue:
    """Lake dictionary — stubbed in Phase 4a, GNIS-backed in Phase 5."""
    q = (request.args.get("q") or "").strip().lower()
    state = (request.args.get("state") or "").strip().upper()

    rows = list(_STUB_LAKES)
    if q:
        rows = [r for r in rows if q in r.name.lower()]
    if state:
        rows = [r for r in rows if state in r.state.upper()]

    return render_template(
        "admin/catalog/lakes.html",
        page_title="Lakes",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Lakes", None),
        ),
        rows=rows,
        q=q,
        state=state,
    )


# ---------------------------------------------------------------------------
# Catalog / Render presets — read-only view of TIER_CONFIG
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RenderPresetRow:
    """One row in the render presets table — flattened view of TierConfig."""

    tier: int
    tier_name: str
    long_edge_px: int
    dpi: int
    fmt: str
    jpeg_quality: int
    watermark: bool
    watermark_text: str
    watermark_opacity: float
    public: bool
    bucket: str
    content_type: str


_TIER_NAMES: dict[int, str] = {1: "Thumbnail", 2: "Preview", 3: "Print"}


@admin_bp.route("/catalog/render-presets", methods=["GET"])
@requires_role("admin")
def catalog_render_presets() -> ResponseReturnValue:
    """Read-only display of the three-tier render config.

    Phase 5 wires editing (saved to a settings table); for now the page
    just shows the locked Phase 2 values from
    :data:`review_app.render.tiers.TIER_CONFIG`.
    """
    rows: list[RenderPresetRow] = []
    # Watermark text/opacity are app-wide constants; not stored per-tier.
    # Pull from env so prod can override without code change.
    watermark_text = os.environ.get("WATERMARK_TEXT", "fishingposter.com")
    try:
        watermark_opacity = float(os.environ.get("WATERMARK_OPACITY", "0.15"))
    except ValueError:
        watermark_opacity = 0.15

    for tier_id, cfg in sorted(TIER_CONFIG.items()):
        rows.append(
            RenderPresetRow(
                tier=tier_id,
                tier_name=_TIER_NAMES.get(tier_id, f"Tier {tier_id}"),
                long_edge_px=cfg.long_edge_px,
                dpi=cfg.dpi,
                fmt=cfg.fmt,
                jpeg_quality=cfg.jpeg_quality,
                watermark=cfg.watermark,
                watermark_text=watermark_text if cfg.watermark else "",
                watermark_opacity=watermark_opacity if cfg.watermark else 0.0,
                public=cfg.public,
                bucket=cfg.bucket(),
                content_type=cfg.content_type,
            )
        )

    return render_template(
        "admin/catalog/render_presets.html",
        page_title="Render presets",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Render presets", None),
        ),
        rows=rows,
    )


__all__: list[str] = []
