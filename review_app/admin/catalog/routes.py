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
from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from review_app import audit
from review_app.admin._helpers import crumbs
from review_app.admin.routes import admin_bp
from review_app.auth.decorators import requires_role
from review_app.db import get_session
from review_app.render.tiers import TIER_CONFIG, reset_cache as _reset_tier_cache

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
# Catalog / Render presets — DB-backed editable view (Phase 6)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RenderPresetRow:
    """One row in the render presets table — flattened view of RenderPreset."""

    tier: int
    tier_name: str
    long_edge_px: int
    dpi: int
    fmt: str
    jpeg_quality: int | None
    watermark: bool
    watermark_text: str
    watermark_opacity: float
    watermark_angle: int
    public: bool
    bucket_env: str
    bucket: str
    content_type: str


_TIER_NAMES: dict[int, str] = {1: "Thumbnail", 2: "Preview", 3: "Print"}


def _load_preset_rows() -> list[RenderPresetRow]:
    """Hydrate the three preset rows from the DB, falling back to TIER_CONFIG."""
    rows: list[RenderPresetRow] = []

    db_rows: dict[int, Any] = {}
    try:
        from review_app.render.presets_model import RenderPreset

        with get_session() as session:
            try:
                fetched = session.execute(select(RenderPreset)).scalars().all()
                db_rows = {int(r.tier): r for r in fetched}
            except (OperationalError, ProgrammingError):
                db_rows = {}
    except ImportError:
        db_rows = {}

    for tier_id, cfg in sorted(TIER_CONFIG.items()):
        db = db_rows.get(tier_id)
        if db is not None:
            fmt_raw = str(db.format)
            quality = int(db.jpeg_quality) if db.jpeg_quality is not None else None
            wm_text = str(db.watermark_text or "")
            wm_opacity = float(db.watermark_opacity) if db.watermark_opacity is not None else 0.0
            wm_angle = int(db.watermark_angle) if db.watermark_angle is not None else 0
            bucket_env = str(db.bucket_env_var)
            long_edge = int(db.long_edge_px)
            dpi = int(db.dpi)
            watermark = bool(db.watermark_enabled)
            public = bool(db.public_read)
        else:
            fmt_raw = "jpeg" if cfg.fmt == "JPEG" else "png"
            quality = cfg.jpeg_quality if cfg.fmt == "JPEG" else None
            wm_text = os.environ.get("WATERMARK_TEXT", "fishingposter.com") if cfg.watermark else ""
            try:
                wm_opacity = float(os.environ.get("WATERMARK_OPACITY", "0.15")) if cfg.watermark else 0.0
            except ValueError:
                wm_opacity = 0.15 if cfg.watermark else 0.0
            wm_angle = 30 if cfg.watermark else 0
            bucket_env = cfg.bucket_env
            long_edge = cfg.long_edge_px
            dpi = cfg.dpi
            watermark = cfg.watermark
            public = cfg.public

        # Resolve bucket name through env, falling back to the hardcoded default.
        bucket_default = TIER_CONFIG[tier_id].default_bucket
        bucket_name = os.environ.get(bucket_env, bucket_default)

        rows.append(
            RenderPresetRow(
                tier=tier_id,
                tier_name=_TIER_NAMES.get(tier_id, f"Tier {tier_id}"),
                long_edge_px=long_edge,
                dpi=dpi,
                fmt=fmt_raw,
                jpeg_quality=quality,
                watermark=watermark,
                watermark_text=wm_text,
                watermark_opacity=wm_opacity,
                watermark_angle=wm_angle,
                public=public,
                bucket_env=bucket_env,
                bucket=bucket_name,
                content_type=f"image/{fmt_raw}",
            )
        )
    return rows


@admin_bp.route("/catalog/render-presets", methods=["GET"])
@requires_role("admin")
def catalog_render_presets() -> ResponseReturnValue:
    """Editable display of the three-tier render config (Phase 6)."""
    return render_template(
        "admin/catalog/render_presets.html",
        page_title="Render presets",
        breadcrumbs=crumbs(
            ("Admin", "/admin"),
            ("Catalog", None),
            ("Render presets", None),
        ),
        rows=_load_preset_rows(),
    )


def _validate_preset_form(
    form: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate POSTed render-preset form fields.

    Returns ``(values, None)`` on success or ``(None, error_message)`` on
    validation failure. Validators mirror the migration's CHECK constraints.
    """
    try:
        long_edge = int(form.get("long_edge_px") or "")
    except (TypeError, ValueError):
        return None, "long_edge_px must be an integer."
    if not (100 <= long_edge <= 12000):
        return None, "long_edge_px must be between 100 and 12000."

    try:
        dpi = int(form.get("dpi") or "")
    except (TypeError, ValueError):
        return None, "dpi must be an integer."
    if not (72 <= dpi <= 600):
        return None, "dpi must be between 72 and 600."

    fmt = str(form.get("format") or "").strip().lower()
    if fmt not in {"jpeg", "png", "webp"}:
        return None, "format must be jpeg, png, or webp."

    quality_raw = (form.get("jpeg_quality") or "").strip()
    quality: int | None
    if quality_raw == "" or fmt != "jpeg":
        quality = None
    else:
        try:
            quality = int(quality_raw)
        except ValueError:
            return None, "jpeg_quality must be an integer or empty."
        if not (50 <= quality <= 95):
            return None, "jpeg_quality must be between 50 and 95."

    watermark_enabled = (form.get("watermark_enabled") or "").lower() in {"1", "true", "on", "yes"}
    public_read = (form.get("public_read") or "").lower() in {"1", "true", "on", "yes"}

    watermark_text = (form.get("watermark_text") or "").strip() or None

    opacity_raw = (form.get("watermark_opacity") or "").strip()
    opacity_val: float | None
    if opacity_raw == "":
        opacity_val = None
    else:
        try:
            opacity_val = float(opacity_raw)
        except ValueError:
            return None, "watermark_opacity must be a decimal between 0 and 1."
        if not (0.0 <= opacity_val <= 1.0):
            return None, "watermark_opacity must be between 0.0 and 1.0."

    angle_raw = (form.get("watermark_angle") or "").strip()
    angle_val: int | None
    if angle_raw == "":
        angle_val = None
    else:
        try:
            angle_val = int(angle_raw)
        except ValueError:
            return None, "watermark_angle must be an integer."
        if not (-180 <= angle_val <= 180):
            return None, "watermark_angle must be between -180 and 180."

    bucket_env_var = (form.get("bucket_env_var") or "").strip()
    if not bucket_env_var:
        return None, "bucket_env_var is required."

    return (
        {
            "long_edge_px": long_edge,
            "dpi": dpi,
            "format": fmt,
            "jpeg_quality": quality,
            "watermark_enabled": watermark_enabled,
            "watermark_text": watermark_text,
            "watermark_opacity": opacity_val,
            "watermark_angle": angle_val,
            "bucket_env_var": bucket_env_var,
            "public_read": public_read,
        },
        None,
    )


@admin_bp.route("/catalog/render-presets/<int:tier>", methods=["POST"])
@requires_role("admin")
def catalog_render_presets_save(tier: int) -> ResponseReturnValue:
    """Save the form for one tier; bust the in-memory cache on success."""
    if tier not in (1, 2, 3):
        flash(f"Unknown tier {tier}.", "error")
        return redirect(url_for("admin.catalog_render_presets"))

    values, err = _validate_preset_form(request.form)
    if err or values is None:
        flash(err or "Invalid form.", "error")
        return redirect(url_for("admin.catalog_render_presets"))

    try:
        from review_app.render.presets_model import RenderPreset
    except ImportError:
        flash("Render presets model unavailable.", "error")
        return redirect(url_for("admin.catalog_render_presets"))

    user_id_str: str | None = None
    try:
        from flask_login import current_user

        if getattr(current_user, "is_authenticated", False):
            user_id_str = str(current_user.id)
    except Exception:
        user_id_str = None

    try:
        with get_session() as session:
            row = session.execute(
                select(RenderPreset).where(RenderPreset.tier == tier)
            ).scalar_one_or_none()
            before: dict[str, Any] = {}
            if row is None:
                row = RenderPreset(tier=tier, **values, updated_by_user_id=user_id_str)
                session.add(row)
            else:
                before = {
                    "long_edge_px": row.long_edge_px,
                    "dpi": row.dpi,
                    "format": row.format,
                    "jpeg_quality": row.jpeg_quality,
                    "watermark_enabled": row.watermark_enabled,
                    "watermark_text": row.watermark_text,
                    "watermark_opacity": (
                        float(row.watermark_opacity)
                        if row.watermark_opacity is not None
                        else None
                    ),
                    "watermark_angle": row.watermark_angle,
                    "bucket_env_var": row.bucket_env_var,
                    "public_read": row.public_read,
                }
                for k, v in values.items():
                    setattr(row, k, v)
                row.updated_by_user_id = user_id_str  # type: ignore[assignment]

            audit.record(
                session,
                action="render_preset.update",
                target_type="render_preset",
                target_id=str(tier),
                before=before,
                after=values,
            )
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("render_preset save failed: %s", exc)
        flash("Save failed (DB error).", "error")
        return redirect(url_for("admin.catalog_render_presets"))

    _reset_tier_cache()
    flash(f"Tier {tier} ({_TIER_NAMES.get(tier, '')}) updated.", "success")
    return redirect(url_for("admin.catalog_render_presets"))


__all__: list[str] = []
