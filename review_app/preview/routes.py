"""Routes for the Phase 2 frame preview compositor.

Routes:
    - ``GET /preview/<spec_hash>`` — renders the configurator page for a real
      render_spec. Looks up the tier-2 watermarked preview URL (parallel
      agent's ``render_outputs`` table — soft-imported so this module loads
      even before that migration lands) and the active SKUs from
      ``prodigi_skus``.
    - ``GET /preview/_demo`` — dev-only demo with a fixed test poster URL.
      404s in production unless ``PREVIEW_DEMO_ENABLED=true``.
    - ``GET /preview/data/frame_skus.json`` — serves the static catalog file
      (kept under /preview to avoid coupling to Flask's ``static`` mount).

The blueprint is registered with prefix ``/preview`` by
``review_app.preview.init_app``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, send_file

preview_bp = Blueprint("preview", __name__, url_prefix="/preview")

# Project-root anchored — review_app/preview/routes.py -> ../../
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_FRAME_SKUS_PATH = _PACKAGE_ROOT / "data" / "frame_skus.json"

# Sizes + finishes — keep in sync with frame_skus.json + the Alembic seed.
SIZES: list[str] = ["12x16", "16x20", "18x24", "24x36"]
SIZE_LABELS: dict[str, str] = {
    "12x16": '12" x 16"',
    "16x20": '16" x 20"',
    "18x24": '18" x 24"',
    "24x36": '24" x 36"',
}

# Display order (and IDs) for the finish picker.
FINISH_ORDER: list[tuple[str, str]] = [
    ("brown", "Brown"),  # default — UI displays as "Walnut" via override below
    ("black", "Black"),
    ("white", "White"),
    ("natural", "Natural"),
    ("antique-silver", "Antique Silver"),
    ("antique-gold", "Antique Gold"),
    ("dark-grey", "Dark Grey"),
    ("light-grey", "Light Grey"),
]

# Marketing override — Prodigi's "Brown" sells better as "Walnut" per the brief.
# Underlying SKU + Prodigi color slug remain "brown".
FINISH_DISPLAY_OVERRIDES: dict[str, str] = {"brown": "Walnut"}

DEFAULT_SIZE = "16x20"
DEFAULT_FINISH = "brown"

# Demo poster — referenced by /preview/_demo. We use the existing sample
# poster shipped with the repo (assets/sample/) which is served via
# review_app/app.py's /static/sample/<filename> route.
DEMO_POSTER_URL = "/static/sample/sample-poster.jpg"


# ---------------------------------------------------------------------------
# Frame catalog file — served from /preview/data/ to avoid touching app.py's
# static_folder configuration. Loaded once and cached in the app context.
# ---------------------------------------------------------------------------

_CATALOG_CACHE: list[dict[str, Any]] | None = None


def _load_catalog() -> list[dict[str, Any]]:
    """Load and cache the frame_skus.json catalog (32 entries)."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        with _FRAME_SKUS_PATH.open("r", encoding="utf-8") as fh:
            _CATALOG_CACHE = json.load(fh)
    return _CATALOG_CACHE


@preview_bp.route("/data/frame_skus.json")
def serve_frame_skus() -> Any:
    """Serve the catalog JSON to the configurator JS."""
    if not _FRAME_SKUS_PATH.exists():  # pragma: no cover — repo guarantees it
        abort(500, description="frame_skus.json missing")
    return send_file(str(_FRAME_SKUS_PATH), mimetype="application/json")


# ---------------------------------------------------------------------------
# SKU price lookup — joins the JSON catalog with the prodigi_skus DB rows.
# ---------------------------------------------------------------------------


def _load_sku_prices() -> dict[str, int | None]:
    """Return ``{internal_sku: retail_price_cents}`` from the DB.

    Returns an empty dict (not an exception) if the DB session is unbound
    or the table doesn't exist yet — keeps the route renderable in dev
    before Phase 1's migration runs.
    """
    try:
        from sqlalchemy import select

        from review_app.db import get_session_factory
        from review_app.prodigi.db_models import ProdigiSku
    except ImportError:
        return {}

    try:
        factory = get_session_factory()
    except Exception:
        return {}

    try:
        with factory() as session:
            rows = session.execute(
                select(ProdigiSku.internal_sku, ProdigiSku.retail_price_cents)
            ).all()
            return {row.internal_sku: row.retail_price_cents for row in rows}
    except Exception:
        return {}


def _build_finish_view() -> list[dict[str, Any]]:
    """Build the per-finish view-model the template iterates.

    Each entry has the swatch asset URL + a ``prices_by_size`` map so the
    JS can flip the price label on size change without a round-trip.
    """
    catalog = _load_catalog()
    prices = _load_sku_prices()

    # Group catalog rows by finish_id so we can compute prices_by_size.
    by_finish: dict[str, list[dict[str, Any]]] = {}
    for entry in catalog:
        by_finish.setdefault(entry["finish_id"], []).append(entry)

    out: list[dict[str, Any]] = []
    for finish_id, _display in FINISH_ORDER:
        rows = by_finish.get(finish_id, [])
        if not rows:
            continue
        first = rows[0]
        # Display name with marketing override (Brown -> Walnut).
        display = FINISH_DISPLAY_OVERRIDES.get(finish_id, first["finish_display"])
        prices_by_size: dict[str, int | str] = {}
        for r in rows:
            cents = prices.get(r["internal_sku"])
            prices_by_size[r["size_inches"]] = cents if cents is not None else ""
        out.append(
            {
                "finish_id": finish_id,
                "finish_display": display,
                "swatch_asset": first["swatch_asset"],
                "prices_by_size": prices_by_size,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Tier-2 preview URL lookup — soft import of render_outputs (parallel agent).
# ---------------------------------------------------------------------------


def _lookup_tier2_preview_url(spec_hash: str) -> str | None:
    """Look up the tier-2 watermarked preview URL for a render_spec hash.

    Joins ``render_specs`` (lookup by ``spec_hash``) → ``render_outputs``
    (filter ``tier=2``) and resolves the storage row to a URL via the
    parallel render-agent's cache helper.

    Soft-imports the parallel agent's modules. Returns ``None`` if any
    piece is unavailable OR if no tier-2 output exists yet for this spec.
    """
    try:
        from sqlalchemy import select

        from review_app.db import get_session_factory

        # Soft-imports — these may not exist if Phase 2 backend hasn't landed.
        # We catch ImportError below; mypy may or may not see them depending on
        # the working tree, so suppress the not-found warning unconditionally.
        from review_app.render.cache import (  # type: ignore[import-not-found,unused-ignore]
            _hit_to_url,
        )
        from review_app.render.db_models import (  # type: ignore[import-not-found,unused-ignore]
            RenderOutputRow,
            RenderSpecRow,
        )
    except ImportError:
        return None

    try:
        factory = get_session_factory()
        with factory() as session:
            stmt = (
                select(RenderOutputRow)
                .join(
                    RenderSpecRow,
                    RenderOutputRow.render_spec_id == RenderSpecRow.id,
                )
                .where(
                    RenderSpecRow.spec_hash == spec_hash,
                    RenderOutputRow.tier == 2,
                )
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            return _hit_to_url(row)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _render_configurator(poster_preview_url: str) -> str:
    return render_template(
        "preview/configurator.html",
        poster_preview_url=poster_preview_url,
        sizes=SIZES,
        size_labels=SIZE_LABELS,
        finishes=_build_finish_view(),
        default_size=DEFAULT_SIZE,
        default_finish=DEFAULT_FINISH,
        frame_data_url="/preview/data/frame_skus.json",
    )


@preview_bp.route("/_demo")
def demo() -> Any:
    """Dev-only demo route. 404 unless PREVIEW_DEMO_ENABLED=true."""
    if os.environ.get("PREVIEW_DEMO_ENABLED", "").lower() not in {"1", "true", "yes"}:
        abort(404)
    return _render_configurator(DEMO_POSTER_URL)


@preview_bp.route("/<spec_hash>")
def configurator(spec_hash: str) -> Any:
    """Render the configurator for a real render_spec hash."""
    # Defensive — render_spec_hash is hex; anything else is bad client input.
    if not spec_hash or len(spec_hash) < 4 or not all(
        c in "0123456789abcdef" for c in spec_hash.lower()
    ):
        abort(404)

    url = _lookup_tier2_preview_url(spec_hash)
    if url is None:
        abort(404)

    return _render_configurator(url)


@preview_bp.route("/_health")
def health() -> Any:
    """Cheap liveness probe; returns the catalog count."""
    return jsonify({"ok": True, "sku_count": len(_load_catalog())})
