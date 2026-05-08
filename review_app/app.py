"""Flask review app for selecting master wildprint illustrations.

Run with:

    python -m review_app.app

Or:

    python review_app/app.py
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

try:
    import stripe  # type: ignore
except ImportError:  # pragma: no cover — stripe is optional at boot time
    stripe = None  # type: ignore

from config.settings import (
    MANIFEST_PATH,
    MASTER_DIR,
    METADATA_DIR,
    NORMALIZED_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    RAW_DIR,
    SPECIES_JSON,
    STYLES_JSON,
)
from poster_layout import (
    EditorialMultiRenderer,
    FileSystemMasterImageLoader,
    LayoutResult,
    MasterImage,
    PlacedItem,
    PosterSpec,
    SpeciesRef,
    get_profile,
    select_layout_engine,
)


# Slugs accepted by the layout-style picker. Anything else falls back to
# "field_guide" — the default since the picker UI shipped.
_VALID_LAYOUT_STYLES = {"field_guide", "vintage_tackle", "hero", "packed", "custom"}


def _resolve_layout_style(raw: object) -> str:
    """Normalize and validate a ``layout_style`` payload field."""
    s = (str(raw or "")).strip().lower()
    return s if s in _VALID_LAYOUT_STYLES else "field_guide"
from scripts.build_manifest import find_record, load_manifest, save_manifest
from scripts.select_master import copy_masters, mark_selected
from webapp.habitat_engine import (
    get_species_by_slugs,
    recommend as habitat_recommend,
    state_to_region,
)

app = Flask(__name__)
# Persist signed-cookie session secret. In dev, a per-process random key is
# fine; in prod, set FLASK_SECRET_KEY in Coolify so unlock cookies survive
# container restarts.
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("WILDPRINT_SECRET_KEY")
    or secrets.token_hex(32)
)

# ---------------------------------------------------------------------------
# Phase 0 + 1 + 2 + 3 wiring: observability, db, auth, AI logging, storage,
# email, render system, Prodigi client (webhook), preview blueprint, cart,
# checkout, orders, refunds, address validation. Each init_app() is a safe
# no-op when its env flag/credentials are absent, so this block is production
# safe even before every env var is populated.
# ---------------------------------------------------------------------------
from review_app import cli as _cli

# Phase 5b additive imports — customer accounts, content blocks, notes.
from review_app.account import init_app as _init_account
from review_app.addresses import init_app as _init_addresses
from review_app.admin import init_app as _init_admin
from review_app.ai import init_app as _init_ai
from review_app.auth import init_app as _init_auth
from review_app.auth.routes import auth_bp as _auth_bp
from review_app.cart import init_app as _init_cart
from review_app.checkout import init_app as _init_checkout
from review_app.content import init_app as _init_content
from review_app.customers import init_app as _init_customers
from review_app.db.session import init_app as _init_db
from review_app.email import init_app as _init_email
from review_app.notes import init_app as _init_notes
from review_app.observability import init_app as _init_obs
from review_app.orders import init_app as _init_orders
from review_app.preview import init_app as _init_preview
from review_app.prodigi import init_app as _init_prodigi
from review_app.refunds import init_app as _init_refunds
from review_app.render import init_app as _init_render
from review_app.storage import init_app as _init_storage

_init_obs(app)
_init_db(app)
_init_auth(app)
app.register_blueprint(_auth_bp)
_init_ai(app)
_init_storage(app)
_init_email(app)
_init_render(app)
_init_prodigi(app)
_init_preview(app)
_init_customers(app)
_init_addresses(app)
_init_cart(app)
_init_checkout(app)
_init_orders(app)
_init_refunds(app)
# Phase 4a — admin shell. Must register AFTER auth so url_for('auth.login')
# inside the @requires_role decorator resolves.
_init_admin(app)
# Phase 5b — additive: customer-facing /account/*, DB-backed content blocks,
# notes table. Registered after admin so blueprint registration order is
# stable and easy to scan.
_init_account(app)
_init_content(app)
_init_notes(app)
# Phase 5a — audit log middleware + Flask-Limiter rate limiting.
# Each is a safe no-op when its dependency (DB / Redis) is missing. Wire
# AFTER the admin/account blueprints so admin POSTs land in the
# auto-capture path; wire BEFORE _cli.register so limiter init isn't
# overshadowed by CLI command registration.
from review_app.audit import init_app as _init_audit
from review_app.limits import init_app as _init_limits

_init_audit(app)
_init_limits(app)
_cli.register(app)

# ---------------------------------------------------------------------------
# Stripe configuration (gracefully degrades when keys are absent)
# ---------------------------------------------------------------------------

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")

if STRIPE_SECRET_KEY and stripe is not None:
    stripe.api_key = STRIPE_SECRET_KEY


def _stripe_ready() -> bool:
    """True only when every piece needed for Checkout is configured."""
    return bool(
        stripe is not None
        and STRIPE_SECRET_KEY
        and STRIPE_PRICE_ID
    )

# ---------------------------------------------------------------------------
# Auth (HTTP Basic) for admin-only routes
# ---------------------------------------------------------------------------

import base64
import hmac
from functools import wraps

_ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def _check_basic_auth(header_value: str) -> bool:
    """Verify HTTP Basic auth header against ADMIN_USER/ADMIN_PASSWORD."""
    if not _ADMIN_PASSWORD:
        # Auth is disabled (no password set) — dev mode
        return True
    if not header_value or not header_value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[6:]).decode("utf-8")
        user, _, pw = decoded.partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user, _ADMIN_USER) and hmac.compare_digest(
        pw, _ADMIN_PASSWORD
    )


def admin_required(view):
    """Require HTTP Basic auth when ADMIN_PASSWORD is set."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _check_basic_auth(request.headers.get("Authorization", "")):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="wildprint admin"'},
            )
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter for expensive public endpoints
# ---------------------------------------------------------------------------

_RATE_BUCKET: dict[tuple[str, str], list[float]] = {}
_RATE_LOCK = threading.Lock()


def rate_limit(max_calls: int, window_seconds: int = 3600):
    """Allow at most ``max_calls`` calls to this endpoint per client IP per window."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            client_ip = (
                request.headers.get("X-Forwarded-For", "")
                .split(",")[0]
                .strip()
                or request.remote_addr
                or "unknown"
            )
            key = (view.__name__, client_ip)
            now = time.time()
            with _RATE_LOCK:
                bucket = _RATE_BUCKET.setdefault(key, [])
                # prune old entries
                cutoff = now - window_seconds
                bucket[:] = [t for t in bucket if t > cutoff]
                if len(bucket) >= max_calls:
                    retry_after = int(bucket[0] + window_seconds - now)
                    return (
                        jsonify({
                            "error": f"Rate limit exceeded ({max_calls} per hour). "
                                     f"Try again in {retry_after}s.",
                        }),
                        429,
                        {"Retry-After": str(retry_after)},
                    )
                bucket.append(now)
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_json(path) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        # Common shape: {"species": [...]} or {"styles": [...]}
        for key in ("species", "styles", "items", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


SPECIES_OVERRIDES_PATH: Path = METADATA_DIR / "species_overrides.json"
ADMIN_SETTINGS_PATH: Path = METADATA_DIR / "admin_settings.json"

# species_scale clamp (Task C): absolute size weights, range 0.7-1.5.
# Applied directly as the species' relative_scale_index in the layout
# engine (no longer a multiplier on top of the catalog's rsi).
SPECIES_SCALE_MIN = 0.7
SPECIES_SCALE_MAX = 1.5
# global_size_variance clamp (Task D): re-shapes the spread of effective
# scales around 1.0. variance=1.0 keeps scales as-is; 0.0 collapses every
# species to 1.0 (uniform); 2.0 doubles the spread.
GLOBAL_VARIANCE_MIN = 0.0
GLOBAL_VARIANCE_MAX = 2.0
# Floor when global variance amplifies a small scale below the clamp.
EFFECTIVE_SCALE_FLOOR = 0.5


def _load_species_overrides() -> dict[str, dict]:
    """Load admin-edited per-species overrides (e.g. species_scale).

    Stored in ``metadata/species_overrides.json`` — a volume-mounted
    location that persists across deploys. Schema is a flat dict keyed
    by slug:

        {"smallmouth_bass": {"species_scale": 1.0}, ...}

    Legacy: older deploys wrote ``scale_override`` (range 0.3-2.5,
    multiplier on rsi). On read, those entries are migrated in-memory
    to ``species_scale`` clamped to 0.7-1.5.
    """
    try:
        with open(SPECIES_OVERRIDES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    migrated: dict[str, dict] = {}
    for slug, entry in data.items():
        if not isinstance(entry, dict):
            continue
        new_entry = dict(entry)
        if "species_scale" not in new_entry and "scale_override" in new_entry:
            try:
                old = float(new_entry.pop("scale_override"))
            except (TypeError, ValueError):
                old = None
            if old is not None:
                new_entry["species_scale"] = max(
                    SPECIES_SCALE_MIN, min(SPECIES_SCALE_MAX, round(old, 4))
                )
        # Clamp existing species_scale into the new range.
        if "species_scale" in new_entry:
            try:
                v = float(new_entry["species_scale"])
                new_entry["species_scale"] = max(
                    SPECIES_SCALE_MIN, min(SPECIES_SCALE_MAX, round(v, 4))
                )
            except (TypeError, ValueError):
                new_entry.pop("species_scale", None)
        migrated[slug] = new_entry
    return migrated


def _save_species_overrides(data: dict[str, dict]) -> None:
    """Persist overrides JSON. Creates METADATA_DIR if missing."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SPECIES_OVERRIDES_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(SPECIES_OVERRIDES_PATH)


def _load_admin_settings() -> dict:
    """Load global admin settings (e.g. global_size_variance).

    Stored in ``metadata/admin_settings.json``. Returns an empty dict
    when the file is missing or unreadable.
    """
    try:
        with open(ADMIN_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _save_admin_settings(data: dict) -> None:
    """Persist the admin settings JSON atomically."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ADMIN_SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(ADMIN_SETTINGS_PATH)


def _global_size_variance() -> float:
    """Current global size variance multiplier. Default 1.0."""
    raw = _load_admin_settings().get("global_size_variance", 1.0)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(GLOBAL_VARIANCE_MIN, min(GLOBAL_VARIANCE_MAX, v))


def _effective_species_scale(species_scale: float, variance: float) -> float:
    """Compute the effective scale for a species given the global variance.

    effective = 1.0 + (species_scale - 1.0) * variance
    Then clamp to a sane floor so 0-variance + small species doesn't go
    sub-visible.
    """
    eff = 1.0 + (float(species_scale) - 1.0) * float(variance)
    return max(EFFECTIVE_SCALE_FLOOR, eff)


# ---------------------------------------------------------------------------
# Lead capture (landing page + paywall)
# ---------------------------------------------------------------------------

LEADS_PATH: Path = METADATA_DIR / "leads.json"
_LEADS_LOCK = threading.Lock()


def _load_leads() -> list[dict]:
    """Read all stored leads from the persistent leads.json file."""
    try:
        with open(LEADS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _write_leads(leads: list[dict]) -> None:
    """Persist the full leads list atomically."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEADS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, sort_keys=False)
    tmp.replace(LEADS_PATH)


def _save_lead(email: str, lake_name: str, state_code: str) -> dict:
    """Append or update a lead by email. Returns the lead record."""
    email = (email or "").strip().lower()
    lake_name = (lake_name or "").strip()
    state_code = (state_code or "").strip().upper()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with _LEADS_LOCK:
        leads = _load_leads()
        existing = next(
            (lead for lead in leads if lead.get("email") == email), None
        )
        if existing is not None:
            # Refresh the lake/state on a return visit; keep paid status.
            if lake_name:
                existing["lake_name"] = lake_name
            if state_code:
                existing["state"] = state_code
            existing["last_seen_at"] = now
            _write_leads(leads)
            return existing
        record = {
            "email": email,
            "lake_name": lake_name,
            "state": state_code,
            "created_at": now,
            "last_seen_at": now,
            "paid": False,
            "stripe_session_id": None,
            "unlocked_at": None,
        }
        leads.append(record)
        _write_leads(leads)
        return record


def _mark_lead_paid(email: str, stripe_session_id: str) -> dict | None:
    """Flip the `paid` flag for the lead with this email. Idempotent."""
    email = (email or "").strip().lower()
    if not email:
        return None
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with _LEADS_LOCK:
        leads = _load_leads()
        existing = next(
            (lead for lead in leads if lead.get("email") == email), None
        )
        if existing is None:
            existing = {
                "email": email,
                "lake_name": "",
                "state": "",
                "created_at": now,
                "last_seen_at": now,
                "paid": True,
                "stripe_session_id": stripe_session_id,
                "unlocked_at": now,
            }
            leads.append(existing)
        else:
            existing["paid"] = True
            existing["stripe_session_id"] = stripe_session_id
            existing["unlocked_at"] = existing.get("unlocked_at") or now
            existing["last_seen_at"] = now
        _write_leads(leads)
        return existing


def _is_paid(email: str) -> bool:
    """True if the email has a paid lead on disk."""
    email = (email or "").strip().lower()
    if not email:
        return False
    for lead in _load_leads():
        if lead.get("email") == email and lead.get("paid"):
            return True
    return False


def load_species() -> list[dict]:
    base = _load_json(SPECIES_JSON)
    overrides = _load_species_overrides()
    if not overrides:
        return base
    merged = []
    for sp in base:
        if not isinstance(sp, dict):
            merged.append(sp)
            continue
        slug = sp.get("slug")
        if slug and slug in overrides:
            ovr = overrides[slug] or {}
            sp = {**sp, **{k: v for k, v in ovr.items() if v is not None}}
        merged.append(sp)
    return merged


def load_styles() -> list[dict]:
    return _load_json(STYLES_JSON)


def safe_load_manifest() -> list[dict]:
    try:
        return load_manifest()
    except FileNotFoundError:
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def get_variations(
    records: list[dict], species_slug: str, style_slug: str
) -> list[dict]:
    matching = [
        r
        for r in records
        if r.get("species_slug") == species_slug
        and r.get("style_slug") == style_slug
    ]
    matching.sort(key=lambda r: int(r.get("variation", 0) or 0))
    return matching


def get_selected(
    records: list[dict], species_slug: str, style_slug: str
) -> dict | None:
    for r in records:
        if (
            r.get("species_slug") == species_slug
            and r.get("style_slug") == style_slug
            and r.get("selected_as_master")
        ):
            return r
    return None


def display_record(
    records: list[dict], species_slug: str, style_slug: str
) -> dict | None:
    """Return the record to use as a display thumbnail for a (species, style)."""
    selected = get_selected(records, species_slug, style_slug)
    if selected is not None:
        return selected
    variations = get_variations(records, species_slug, style_slug)
    return variations[0] if variations else None


def record_image_relpath(record: dict) -> str | None:
    """Prefer normalized path if it exists on disk, fall back to raw."""
    normalized = record.get("normalized_path")
    raw = record.get("raw_path")
    project_root = Path(PROJECT_ROOT)

    for candidate in (normalized, raw):
        if not candidate:
            continue
        p = Path(candidate)
        abs_p = p if p.is_absolute() else (project_root / p)
        if abs_p.exists():
            try:
                return str(abs_p.resolve().relative_to(project_root.resolve()))
            except ValueError:
                continue
    # No file exists — still return something so template can attempt it
    return normalized or raw


def _find_style(styles: list[dict], slug: str) -> dict | None:
    for s in styles:
        if s.get("slug") == slug:
            return s
    return None


def _find_species(species: list[dict], slug: str) -> dict | None:
    for sp in species:
        if sp.get("slug") == slug:
            return sp
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/browse")
@admin_required
def browse():
    records = safe_load_manifest()
    styles = load_styles()
    species = load_species()

    style_stats = []
    for style in styles:
        slug = style.get("slug")
        style_records = [r for r in records if r.get("style_slug") == slug]
        species_in_style = {r.get("species_slug") for r in style_records}
        selected_species = {
            r.get("species_slug")
            for r in style_records
            if r.get("selected_as_master")
        }
        style_stats.append(
            {
                "slug": slug,
                "name": style.get("style_name") or style.get("name") or slug,
                "description": style.get("description", ""),
                "total_images": len(style_records),
                "species_covered": len(species_in_style),
                "total_species": len(species),
                "selected_count": len(selected_species),
            }
        )

    return render_template(
        "index.html",
        style_stats=style_stats,
        manifest_empty=not records,
        total_records=len(records),
    )


# ---------------------------------------------------------------------------
# Console: command dispatch + SSE streaming
# ---------------------------------------------------------------------------


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

_VALID_PROVIDERS = {"mock", "openai", "recraft"}
_VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
_VALID_QUALITY = {"low", "medium", "high", "auto"}
_VALID_LAYOUTS = {"scaled_row", "grid"}
_VALID_GROUP_BY = {"none", "habitat", "scientific_family"}
_VALID_CANVAS_PRESETS = {
    "tabloid_landscape",
    "tabloid_portrait",
    "poster_24x36",
    "poster_36x24",
}
_VALID_CATEGORIES = {"fish", "turtle", "bird"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "on", "yes"}


def _species_slugs() -> set[str]:
    return {sp.get("slug") for sp in load_species() if sp.get("slug")}


def _style_slugs() -> set[str]:
    return {s.get("slug") for s in load_styles() if s.get("slug")}


def _validate_species_slug(slug: str) -> None:
    if not slug:
        return
    if slug not in _species_slugs():
        raise ValueError(f"Unknown species slug: {slug!r}")


def _validate_style_slug(slug: str) -> None:
    if not slug:
        return
    if slug not in _style_slugs():
        raise ValueError(f"Unknown style slug: {slug!r}")


def _validate_output_path(raw: str) -> str:
    """Resolve an --output path against PROJECT_ROOT; reject escapes."""
    project_root = Path(PROJECT_ROOT).resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(
            f"--output path escapes project root: {raw!r}"
        ) from exc
    return str(resolved)


def _build_argv(cmd_id: str, form: dict) -> list[str]:
    """Whitelist mapping form submissions to subprocess argv lists.

    Raises ValueError on unknown cmd_id, invalid values, or missing required
    fields. The caller should translate that to an HTTP 400.
    """
    if cmd_id == "generate":
        argv = ["python3", "-m", "scripts.batch_generate"]
        species = (form.get("species") or "").strip()
        style = (form.get("style") or "").strip()
        _validate_species_slug(species)
        _validate_style_slug(style)
        if species:
            argv += ["--species", species]
        if style:
            argv += ["--style", style]
        variations = (form.get("variations") or "").strip()
        if variations:
            try:
                n = int(variations)
            except ValueError as exc:
                raise ValueError("variations must be an integer") from exc
            if n < 1 or n > 12:
                raise ValueError("variations must be between 1 and 12")
            argv += ["--variations", str(n)]
        provider = (form.get("provider") or "").strip()
        if provider:
            if provider not in _VALID_PROVIDERS:
                raise ValueError(f"invalid provider: {provider!r}")
            argv += ["--provider", provider]
        model = (form.get("model") or "").strip()
        if model:
            # Model names are free-form per OpenAI/Recraft; keep it simple but
            # reject shell metacharacters for defence in depth.
            if any(c in model for c in [";", "|", "&", "$", "`", "\n", " "]):
                raise ValueError("invalid model name")
            argv += ["--model", model]
        size = (form.get("size") or "").strip()
        if size:
            if size not in _VALID_SIZES:
                raise ValueError(f"invalid size: {size!r}")
            argv += ["--size", size]
        quality = (form.get("quality") or "").strip()
        if quality:
            if quality not in _VALID_QUALITY:
                raise ValueError(f"invalid quality: {quality!r}")
            argv += ["--quality", quality]
        seed = (form.get("seed") or "").strip()
        if seed:
            try:
                argv += ["--seed", str(int(seed))]
            except ValueError as exc:
                raise ValueError("seed must be an integer") from exc
        if _truthy(form.get("dry_run")):
            argv += ["--dry-run"]
        return argv

    if cmd_id == "pipeline":
        # End-to-end pipeline: generate -> normalize -> manifest -> copy
        # masters -> build variants. Used by the admin "Generate Selected"
        # button so a freshly-generated species lands in output/master/...
        # AND output/master_thumbs/... in one shot, without the admin
        # having to remember to also click Normalize + Copy Masters.
        argv = ["python3", "-m", "scripts.run_pipeline"]
        species = (form.get("species") or "").strip()
        style = (form.get("style") or "").strip()
        _validate_species_slug(species)
        _validate_style_slug(style)
        if species:
            argv += ["--species", species]
        if style:
            argv += ["--style", style]
        variations = (form.get("variations") or "").strip()
        if variations:
            try:
                n = int(variations)
            except ValueError as exc:
                raise ValueError("variations must be an integer") from exc
            if n < 1 or n > 12:
                raise ValueError("variations must be between 1 and 12")
            argv += ["--variations", str(n)]
        provider = (form.get("provider") or "").strip()
        if provider:
            if provider not in _VALID_PROVIDERS:
                raise ValueError(f"invalid provider: {provider!r}")
            argv += ["--provider", provider]
        size = (form.get("size") or "").strip()
        if size:
            if size not in _VALID_SIZES:
                raise ValueError(f"invalid size: {size!r}")
            argv += ["--size", size]
        quality = (form.get("quality") or "").strip()
        if quality:
            if quality not in _VALID_QUALITY:
                raise ValueError(f"invalid quality: {quality!r}")
            argv += ["--quality", quality]
        if _truthy(form.get("skip_generate")):
            argv += ["--skip-generate"]
        if _truthy(form.get("force_variants")):
            argv += ["--force-variants"]
        return argv

    if cmd_id == "build-variants":
        # Standalone variants-only rebuild (no generation). Useful on first
        # deploy of the variant infrastructure to backfill thumbs/previews
        # for every existing master.
        argv = ["python3", "-m", "scripts.build_image_variants"]
        species = (form.get("species") or "").strip()
        style = (form.get("style") or "").strip()
        if species:
            _validate_species_slug(species)
            argv += ["--slug", species]
        if style:
            _validate_style_slug(style)
            argv += ["--style", style]
        if _truthy(form.get("force")):
            argv += ["--force"]
        return argv

    if cmd_id == "normalize":
        argv = ["python3", "-m", "scripts.normalize_images"]
        species = (form.get("species") or "").strip()
        style = (form.get("style") or "").strip()
        _validate_species_slug(species)
        _validate_style_slug(style)
        if species:
            argv += ["--species", species]
        if style:
            argv += ["--style", style]
        if _truthy(form.get("force")):
            argv += ["--force"]
        if _truthy(form.get("dry_run")):
            argv += ["--dry-run"]
        return argv

    if cmd_id == "build-manifest":
        return ["python3", "-m", "scripts.build_manifest", "--verbose"]

    if cmd_id == "copy-masters":
        return ["python3", "-m", "scripts.select_master", "--copy"]

    if cmd_id == "render-poster":
        argv = ["python3", "-m", "scripts.render_poster"]
        style = (form.get("style") or "").strip()
        if not style:
            raise ValueError("--style is required for render-poster")
        _validate_style_slug(style)
        argv += ["--style", style]
        title = (form.get("title") or "").strip()
        if not title:
            raise ValueError("--title is required for render-poster")
        argv += ["--title", title]
        subtitle = (form.get("subtitle") or "").strip()
        if subtitle:
            argv += ["--subtitle", subtitle]
        # --species: comma-separated slugs (optional, blank = all enabled).
        species_raw = (form.get("species") or "").strip()
        if species_raw:
            slugs = [s.strip() for s in species_raw.split(",") if s.strip()]
            for s in slugs:
                _validate_species_slug(s)
            argv += ["--species", ",".join(slugs)]
        # --background: hex color from the color picker or text input.
        # Validate shape so we don't pass anything weird through to the CLI.
        background = (form.get("background") or "").strip()
        if background:
            if not re.fullmatch(r"#?[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", background):
                raise ValueError(
                    f"invalid background color {background!r}; "
                    "must be a 3- or 6-digit hex (e.g. #FFFFFF)"
                )
            if not background.startswith("#"):
                background = "#" + background
            argv += ["--background", background]
        # --editorial: single-subject hero layout + EditorialPosterRenderer.
        if _truthy(form.get("editorial")):
            argv += ["--editorial"]
        layout = (form.get("layout") or "").strip()
        if layout and not _truthy(form.get("editorial")):
            if layout not in _VALID_LAYOUTS:
                raise ValueError(f"invalid layout: {layout!r}")
            argv += ["--layout", layout]
        category = (form.get("category") or "").strip()
        if category:
            if category not in _VALID_CATEGORIES:
                raise ValueError(f"invalid category: {category!r}")
            argv += ["--category", category]
        group_by = (form.get("group_by") or "").strip()
        if group_by:
            if group_by not in _VALID_GROUP_BY:
                raise ValueError(f"invalid group-by: {group_by!r}")
            argv += ["--group-by", group_by]
        canvas_preset = (form.get("canvas_preset") or "").strip()
        if canvas_preset:
            if canvas_preset not in _VALID_CANVAS_PRESETS:
                raise ValueError(f"invalid canvas preset: {canvas_preset!r}")
            argv += ["--canvas-preset", canvas_preset]
        mprf = (form.get("max_per_row_fraction") or "").strip()
        if mprf:
            try:
                float(mprf)
            except ValueError as exc:
                raise ValueError(
                    "max-per-row-fraction must be a number"
                ) from exc
            argv += ["--max-per-row-fraction", mprf]
        if _truthy(form.get("no_labels")):
            argv += ["--no-labels"]
        bg_image_filename = (form.get("background_image_filename") or "").strip()
        if bg_image_filename:
            if not re.fullmatch(r"[\w.\-]+", bg_image_filename):
                raise ValueError("invalid background_image_filename")
            bg_path = (
                Path(PROJECT_ROOT) / "output" / "uploads" / bg_image_filename
            )
            if not bg_path.is_file():
                raise ValueError(
                    f"background image not found: {bg_image_filename}"
                )
            argv += ["--background-image", str(bg_path)]
        output = (form.get("output") or "").strip()
        if output:
            argv += ["--output", _validate_output_path(output)]
        return argv

    raise ValueError(f"unknown command id: {cmd_id!r}")


def _trim_old_jobs(max_age_seconds: int = 3600) -> None:
    """Drop JOBS entries older than max_age_seconds (based on started_at)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    with JOBS_LOCK:
        stale = []
        for jid, job in JOBS.items():
            try:
                started = datetime.fromisoformat(job.get("started_at", ""))
            except ValueError:
                continue
            if started < cutoff:
                stale.append(jid)
        for jid in stale:
            JOBS.pop(jid, None)


def _reader_thread(job_id: str) -> None:
    """Read subprocess stdout line-by-line into the job's lines list."""
    job = JOBS.get(job_id)
    if job is None:
        return
    proc: subprocess.Popen = job["proc"]
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with job["lock"]:
                job["lines"].append(line)
    except Exception as exc:  # pragma: no cover - defensive
        with job["lock"]:
            job["lines"].append(f"[reader error: {exc}]")
    finally:
        proc.wait()
        with job["lock"]:
            job["status"] = "done"
            job["exit_code"] = proc.returncode
            job["finished_at"] = _now_iso()


def _start_job(cmd_id: str, argv: list[str]) -> str:
    """Spawn a subprocess for argv and return a new job id."""
    _trim_old_jobs()
    job_id = uuid.uuid4().hex
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    job = {
        "cmd": cmd_id,
        "argv": argv,
        "proc": proc,
        "lines": [],
        "status": "running",
        "exit_code": None,
        "started_at": _now_iso(),
        "finished_at": None,
        "lock": threading.Lock(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    t = threading.Thread(target=_reader_thread, args=(job_id,), daemon=True)
    t.start()
    return job_id


def _iter_job_lines(job_id: str) -> Iterator[str]:
    """SSE generator: yield already-buffered lines, then tail new ones."""
    job = JOBS.get(job_id)
    if job is None:
        yield "event: error\ndata: unknown job\n\n"
        return
    sent = 0
    while True:
        with job["lock"]:
            lines = list(job["lines"])
            status = job["status"]
            exit_code = job["exit_code"]
        new = lines[sent:]
        for line in new:
            # Escape any accidental SSE terminators.
            safe = line.replace("\r", "")
            yield f"data: {safe}\n\n"
        sent = len(lines)
        if status == "done":
            yield (
                "event: done\n"
                f"data: {{\"exit_code\": {exit_code if exit_code is not None else 'null'}}}\n\n"
            )
            return
        time.sleep(0.25)


@app.route("/")
def landing():
    """Public landing page — sells the product and captures the lead."""
    return render_template(
        "landing.html",
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
    )


@app.route("/api/lead", methods=["POST"])
@rate_limit(60)
def api_lead():
    """Persist a lead and return the wizard redirect URL.

    Primary input: ``zip_code`` (5 digits). The handler resolves it to a
    state abbreviation via Smarty (with a static prefix-table fallback),
    persists the resolved state on the lead, and redirects to ``/create``
    with the legacy ``state=...`` query param so downstream code keeps
    working unchanged.

    Backward compat: if the client sends ``state`` instead of (or alongside)
    ``zip_code`` we honor it but emit a deprecation log line.
    """
    from review_app.addresses.zip_resolver import is_valid_zip, resolve_zip

    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    lake_name = (data.get("lake_name") or "").strip()
    zip_code = (data.get("zip_code") or "").strip()
    legacy_state = (data.get("state") or "").strip().upper()

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required."}), 400
    if not lake_name:
        return jsonify({"error": "Lake name required."}), 400

    state_code = ""
    resolution_source = ""
    if zip_code:
        if not is_valid_zip(zip_code):
            return jsonify({"error": "ZIP code must be 5 digits."}), 400
        resolved = resolve_zip(zip_code)
        state_code = resolved["state"]
        resolution_source = resolved["source"]
        if not state_code:
            return jsonify({"error": "Could not resolve ZIP to a US state."}), 400
    elif legacy_state:
        # Backward compat: callers that still POST ``state`` (older clients,
        # tests). Log so we can spot stragglers in production.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "api_lead: legacy 'state' field used (deprecated; prefer zip_code). state=%s",
            legacy_state,
        )
        state_code = legacy_state
        resolution_source = "legacy_state"
    else:
        return jsonify({"error": "ZIP code required."}), 400

    _save_lead(email, lake_name, state_code)
    # Remember the email in the session so /api/me knows who's coming back.
    session["email"] = email
    if _is_paid(email):
        session["unlocked"] = True

    from urllib.parse import urlencode

    qs_params = {
        "lake": lake_name,
        "state": state_code,
        "email": email,
    }
    if zip_code:
        qs_params["zip"] = zip_code
    qs = urlencode(qs_params)
    return jsonify({
        "redirect": f"/create?{qs}",
        "state": state_code,
        "zip_code": zip_code,
        "source": resolution_source,
    })


@app.route("/api/me")
def api_me():
    """Return current unlock state and known email."""
    email = session.get("email") or ""
    unlocked = bool(session.get("unlocked"))
    # If session lost the unlock but the lead is paid, restore it.
    if not unlocked and email and _is_paid(email):
        session["unlocked"] = True
        unlocked = True
    return jsonify({
        "unlocked": unlocked,
        "email": email or None,
    })


@app.route("/api/create-checkout-session", methods=["POST"])
@rate_limit(20)
def api_create_checkout_session():
    """Create a Stripe Checkout session for the $49 unlock."""
    if not _stripe_ready():
        return jsonify({"error": "Payment not yet configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or session.get("email") or "").strip().lower()
    lake_name = (data.get("lake_name") or "").strip()
    state_code = (data.get("state") or "").strip().upper()
    if not email:
        return jsonify({"error": "Email required to start checkout."}), 400

    # Make sure we have a lead row to flip to paid later.
    _save_lead(email, lake_name, state_code)
    session["email"] = email

    success_url = url_for(
        "checkout_success", _external=True
    ) + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = url_for("create", _external=True)
    if lake_name or state_code:
        from urllib.parse import urlencode
        cancel_url += "?" + urlencode({
            "lake": lake_name,
            "state": state_code,
            "email": email,
        })

    try:
        checkout = stripe.checkout.Session.create(  # type: ignore[union-attr]
            mode="payment",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "email": email,
                "lake_name": lake_name,
                "state": state_code,
            },
        )
    except Exception as exc:
        return jsonify({"error": f"Stripe error: {exc}"}), 502
    return jsonify({"url": checkout.url})


@app.route("/checkout/success")
def checkout_success():
    """Verify the Stripe session and flip the unlock cookie."""
    session_id = request.args.get("session_id", "").strip()
    paid_email = ""
    lake_name = ""
    state_code = ""
    payment_status = "unknown"

    if _stripe_ready() and session_id:
        try:
            cs = stripe.checkout.Session.retrieve(session_id)  # type: ignore[union-attr]
            payment_status = cs.get("payment_status", "unknown")
            meta = cs.get("metadata") or {}
            paid_email = (
                meta.get("email")
                or cs.get("customer_email")
                or cs.get("customer_details", {}).get("email")
                or ""
            ).strip().lower()
            lake_name = (meta.get("lake_name") or "").strip()
            state_code = (meta.get("state") or "").strip().upper()
            if payment_status == "paid" and paid_email:
                _mark_lead_paid(paid_email, session_id)
                session["email"] = paid_email
                session["unlocked"] = True
        except Exception:
            payment_status = "verify_failed"

    from urllib.parse import urlencode
    qs = urlencode({
        k: v for k, v in {
            "lake": lake_name,
            "state": state_code,
            "email": paid_email,
        }.items() if v
    })
    continue_url = "/create" + (f"?{qs}" if qs else "")
    return render_template(
        "checkout_success.html",
        payment_status=payment_status,
        continue_url=continue_url,
        email=paid_email,
    )


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """Receive Stripe webhook events as a defense-in-depth confirmation."""
    if stripe is None or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 503

    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(  # type: ignore[union-attr]
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        return jsonify({"error": "Invalid signature"}), 400

    if event.get("type") == "checkout.session.completed":
        cs = event["data"]["object"]
        meta = cs.get("metadata") or {}
        email = (
            meta.get("email")
            or cs.get("customer_email")
            or cs.get("customer_details", {}).get("email")
            or ""
        ).strip().lower()
        if email and cs.get("payment_status") == "paid":
            _mark_lead_paid(email, cs.get("id", ""))

    return jsonify({"ok": True})


@app.route("/console")
@admin_required
def console():
    """Render the command console with status bar, forms, log, and gallery."""
    species = load_species()
    styles = load_styles()
    records = safe_load_manifest()

    enabled_species = [sp for sp in species if sp.get("enabled") is not False]
    enabled_styles = [s for s in styles if s.get("enabled") is not False]

    # Category counts
    category_counts: dict[str, int] = {}
    for sp in enabled_species:
        cat = sp.get("category", "other")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    masters_count = sum(1 for r in records if r.get("selected_as_master"))

    # API key probe — only emit boolean, never the key
    api_key_loaded = bool(os.getenv("OPENAI_API_KEY"))

    # Master image status per species (for the Missing Masters panel)
    master_dir = Path(MASTER_DIR)
    style_slugs = [s.get("slug") for s in enabled_styles]
    species_master_status = []
    missing_count = 0
    has_all_count = 0
    for sp in enabled_species:
        slug = sp.get("slug", "")
        cat = sp.get("category", "")
        has = {st: (master_dir / st / f"{slug}.png").exists() for st in style_slugs}
        has_any = any(has.values())
        has_all = all(has.values())
        if has_all:
            has_all_count += 1
        if not has_all:
            missing_count += 1
        species_master_status.append({
            "slug": slug,
            "common_name": sp.get("common_name", slug),
            "category": cat,
            "has_master": has,
            "has_any": has_any,
            "has_all": has_all,
        })

    status = {
        "species_total": len(enabled_species),
        "category_counts": category_counts,
        "styles_total": len(enabled_styles),
        "style_names": [
            s.get("style_name") or s.get("name") or s.get("slug")
            for s in enabled_styles
        ],
        "masters_count": masters_count,
        "manifest_count": len(records),
        "api_key_loaded": api_key_loaded,
        "has_all_masters": has_all_count,
        "missing_masters": missing_count,
    }

    return render_template(
        "console.html",
        status=status,
        species=enabled_species,
        styles=enabled_styles,
        species_master_status=species_master_status,
    )


@app.route("/run/<cmd_id>", methods=["POST"])
@admin_required
def run_command(cmd_id: str):
    try:
        argv = _build_argv(cmd_id, request.form.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        job_id = _start_job(cmd_id, argv)
    except FileNotFoundError as exc:
        return jsonify({"error": f"failed to spawn: {exc}"}), 500
    return jsonify({"job_id": job_id, "cmd": cmd_id, "argv": argv})


@app.route("/stream/<job_id>")
@admin_required
def stream_job(job_id: str):
    if job_id not in JOBS:
        abort(404)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream",
    }
    return Response(_iter_job_lines(job_id), headers=headers)


@app.route("/job/<job_id>")
@admin_required
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    with job["lock"]:
        return jsonify(
            {
                "status": job["status"],
                "exit_code": job["exit_code"],
                "lines": list(job["lines"]),
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
                "cmd": job["cmd"],
                "argv": job["argv"],
            }
        )


@app.route("/recent-images")
@admin_required
def recent_images():
    """Return the 24 most recent raw PNGs across all species/styles."""
    project_root = Path(PROJECT_ROOT).resolve()
    raw_root = Path(RAW_DIR)
    if not raw_root.exists():
        return jsonify({"images": []})

    species_by_slug = {sp.get("slug"): sp for sp in load_species()}
    styles_by_slug = {s.get("slug"): s for s in load_styles()}

    entries = []
    # Layout: raw/{style_slug}/{species_slug}/{species}_{style}_v{n}.png
    for png in raw_root.rglob("*.png"):
        try:
            stat = png.stat()
        except OSError:
            continue
        rel = png.resolve()
        try:
            rel_str = str(rel.relative_to(project_root))
        except ValueError:
            continue
        parts = png.relative_to(raw_root).parts
        style_slug = parts[0] if len(parts) >= 1 else ""
        species_slug = parts[1] if len(parts) >= 2 else ""
        # Derive variation from filename suffix _v{n}
        stem = png.stem
        variation: int | None = None
        if "_v" in stem:
            tail = stem.rsplit("_v", 1)[-1]
            try:
                variation = int(tail)
            except ValueError:
                variation = None
        sp = species_by_slug.get(species_slug) or {}
        st = styles_by_slug.get(style_slug) or {}
        entries.append(
            {
                "species_slug": species_slug,
                "species_common_name": sp.get("common_name", species_slug),
                "style_slug": style_slug,
                "style_name": st.get("style_name")
                or st.get("name")
                or style_slug,
                "variation": variation,
                "raw_path": rel_str,
                "mtime_iso": datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC
                ).isoformat(timespec="seconds"),
                "mtime": stat.st_mtime,
            }
        )

    entries.sort(key=lambda e: e["mtime"], reverse=True)
    entries = entries[:24]
    for e in entries:
        e.pop("mtime", None)
    return jsonify({"images": entries})


@app.route("/recent-posters")
@admin_required
def recent_posters():
    """Return the 12 most recent rendered posters from output/posters/."""
    project_root = Path(PROJECT_ROOT).resolve()
    posters_root = (project_root / "output" / "posters").resolve()
    if not posters_root.exists():
        return jsonify({"posters": []})

    entries = []
    for png in posters_root.glob("*.png"):
        try:
            stat = png.stat()
        except OSError:
            continue
        resolved = png.resolve()
        try:
            rel_str = str(resolved.relative_to(project_root))
        except ValueError:
            continue
        # Human-friendly label: strip the extension and replace underscores.
        label = png.stem.replace("_", " ")
        entries.append(
            {
                "filename": png.name,
                "label": label,
                "path": rel_str,
                "bytes": stat.st_size,
                "mtime_iso": datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC
                ).isoformat(timespec="seconds"),
                "mtime": stat.st_mtime,
            }
        )

    entries.sort(key=lambda e: e["mtime"], reverse=True)
    entries = entries[:12]
    for e in entries:
        e.pop("mtime", None)
    return jsonify({"posters": entries})


# ---------------------------------------------------------------------------
# Browse (legacy index) and related routes
# ---------------------------------------------------------------------------


@app.route("/style/<style_slug>")
@admin_required
def style_view(style_slug: str):
    records = safe_load_manifest()
    styles = load_styles()
    species = load_species()

    style = _find_style(styles, style_slug)
    if style is None:
        abort(404)

    cards = []
    for sp in species:
        sp_slug = sp.get("slug")
        variations = get_variations(records, sp_slug, style_slug)
        if not variations:
            continue
        display = display_record(records, sp_slug, style_slug)
        cards.append(
            {
                "species_slug": sp_slug,
                "common_name": sp.get("common_name", sp_slug),
                "scientific_name": sp.get("scientific_name", ""),
                "variation_count": len(variations),
                "display": display,
                "display_relpath": record_image_relpath(display) if display else None,
                "has_selected": get_selected(records, sp_slug, style_slug)
                is not None,
            }
        )

    return render_template(
        "style.html",
        style=style,
        cards=cards,
        manifest_empty=not records,
    )


@app.route("/species/<species_slug>")
@admin_required
def species_view(species_slug: str):
    records = safe_load_manifest()
    styles = load_styles()
    species = load_species()

    sp = _find_species(species, species_slug)
    if sp is None:
        abort(404)

    style_cards = []
    for style in styles:
        if style.get("enabled") is False:
            continue
        slug = style.get("slug")
        variations = get_variations(records, species_slug, slug)
        display = display_record(records, species_slug, slug)
        style_cards.append(
            {
                "style_slug": slug,
                "style_name": style.get("name", slug),
                "description": style.get("description", ""),
                "variation_count": len(variations),
                "display": display,
                "display_relpath": record_image_relpath(display) if display else None,
                "has_selected": get_selected(records, species_slug, slug) is not None,
            }
        )

    return render_template(
        "species.html",
        species=sp,
        style_cards=style_cards,
        manifest_empty=not records,
    )


@app.route("/review/<style_slug>/<species_slug>")
@admin_required
def review_view(style_slug: str, species_slug: str):
    records = safe_load_manifest()
    styles = load_styles()
    species = load_species()

    style = _find_style(styles, style_slug)
    sp = _find_species(species, species_slug)
    if style is None or sp is None:
        abort(404)

    variations = get_variations(records, species_slug, style_slug)
    selected = get_selected(records, species_slug, style_slug)
    selected_variation = selected.get("variation") if selected else None

    cards = []
    for r in variations:
        cards.append(
            {
                "record": r,
                "relpath": record_image_relpath(r),
                "is_selected": bool(r.get("selected_as_master")),
            }
        )

    return render_template(
        "review.html",
        style=style,
        species=sp,
        cards=cards,
        selected_variation=selected_variation,
        manifest_empty=not records,
    )


@app.route("/select", methods=["POST"])
@admin_required
def select():
    species_slug = request.form.get("species_slug", "").strip()
    style_slug = request.form.get("style_slug", "").strip()
    variation_raw = request.form.get("variation", "").strip()

    if not species_slug or not style_slug or not variation_raw:
        flash("Missing species, style, or variation.", "error")
        return redirect(url_for("browse"))

    try:
        variation = int(variation_raw)
    except ValueError:
        flash(f"Invalid variation number: {variation_raw!r}.", "error")
        return redirect(url_for("browse"))

    try:
        mark_selected(species_slug, style_slug, variation)
        flash(
            f"Selected v{variation} as master for "
            f"{species_slug} × {style_slug}.",
            "success",
        )
    except Exception as exc:  # pragma: no cover - defensive
        flash(f"Failed to mark selection: {exc}", "error")

    return redirect(
        url_for("review_view", style_slug=style_slug, species_slug=species_slug)
    )


@app.route("/copy-masters", methods=["POST"])
@admin_required
def copy_masters_route():
    try:
        copy_masters()
        flash("Master images copied successfully.", "success")
    except Exception as exc:  # pragma: no cover - defensive
        flash(f"Failed to copy masters: {exc}", "error")
    return redirect(url_for("browse"))


@app.route("/static/habitat/<filename>")
def serve_habitat_image(filename: str):
    """Serve habitat preview images from assets/habitat/."""
    safe = re.sub(r"[^\w.\-]", "_", filename)
    p = Path(PROJECT_ROOT) / "assets" / "habitat" / safe
    if not p.exists() or not p.is_file():
        abort(404)
    return send_file(str(p))


@app.route("/static/sample/<filename>")
def serve_sample_image(filename: str):
    """Serve sample poster images from assets/sample/."""
    safe = re.sub(r"[^\w.\-]", "_", filename)
    p = Path(PROJECT_ROOT) / "assets" / "sample" / safe
    if not p.exists() or not p.is_file():
        abort(404)
    return send_file(str(p))


@app.route("/static/frame_overlays/<filename>")
def serve_frame_overlay(filename: str):
    """Serve transparent mitered frame overlay PNGs from assets/frame_overlays/."""
    safe = re.sub(r"[^\w.\-]", "_", filename)
    p = Path(PROJECT_ROOT) / "assets" / "frame_overlays" / safe
    if not p.exists() or not p.is_file():
        abort(404)
    return send_file(str(p), mimetype="image/png")


@app.route("/image/<path:relpath>")
def image(relpath: str):
    project_root = Path(PROJECT_ROOT).resolve()
    requested = (project_root / relpath).resolve()

    try:
        requested.relative_to(project_root)
    except ValueError:
        abort(404)

    if not requested.exists() or not requested.is_file():
        abort(404)

    return send_file(str(requested))


# Slug shape used by the variant routes below: lowercase letters, digits,
# underscores. Same ruleset as _validate_species_slug / _validate_style_slug.
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


def _serve_variant(style: str, slug: str, kind: str):
    """Shared body for ``/thumb/<style>/<slug>`` and ``/preview/<style>/<slug>``.

    Falls back to the master PNG if the variant is missing — this means a
    new master shows up immediately even if the variant build hasn't run
    yet, at the cost of a one-page slow load until the next variant build.
    """
    if not _SAFE_SLUG_RE.match(style) or not _SAFE_SLUG_RE.match(slug):
        abort(404)

    if kind == "thumb":
        rel = Path("master_thumbs") / style / f"{slug}.jpg"
        mimetype = "image/jpeg"
    elif kind == "preview":
        rel = Path("master_previews") / style / f"{slug}.webp"
        mimetype = "image/webp"
    else:
        abort(404)

    output_root = Path(OUTPUT_DIR).resolve()
    target = (output_root / rel).resolve()
    try:
        target.relative_to(output_root)
    except ValueError:
        abort(404)

    if not target.exists() or not target.is_file():
        # Fall back to the master PNG so the picker still shows something
        # while a fresh variant build is in flight.
        master = (output_root / "master" / style / f"{slug}.png").resolve()
        try:
            master.relative_to(output_root)
        except ValueError:
            abort(404)
        if not master.exists() or not master.is_file():
            abort(404)
        resp = send_file(str(master), mimetype="image/png")
        # Short cache on the fallback so a freshly built variant takes over.
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp

    resp = send_file(str(target), mimetype=mimetype)
    # Variants are derived from a master at a known size+quality. They're
    # safe to cache aggressively — a new master overwrites the variant on
    # the next pipeline run, and clients use the URL path (which always
    # changes when the master itself is replaced as part of a "regenerate"
    # flow because the mtime changes; aggressive caching is acceptable
    # because the user will hard-reload after regenerating).
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/thumb/<style>/<slug>")
def thumb(style: str, slug: str):
    """Serve a 256px JPEG thumbnail of the master for the species picker."""
    return _serve_variant(style, slug, "thumb")


@app.route("/preview/<style>/<slug>")
def preview(style: str, slug: str):
    """Serve a 1024px WebP preview of the master for the live editor canvas."""
    return _serve_variant(style, slug, "preview")


# ---------------------------------------------------------------------------
# Poster creator routes
# ---------------------------------------------------------------------------


@app.route("/create")
def create():
    """Render the poster creator page."""
    species = load_species()
    styles = load_styles()
    # Read the optional ?lake= query string and pass it through so the
    # title input + loading-overlay default render with the lake name on
    # the very first paint (Bug 2 fix — previously the static template
    # default "[your water]" was visible until JS ran, which created a
    # visible flash for users arriving with ?lake=...).
    lake_name = (request.args.get("lake", "") or "").strip()
    return render_template(
        "create.html",
        species=species,
        styles=styles,
        lake_name=lake_name,
    )


@app.route("/api/recommend", methods=["POST"])
@rate_limit(60)
def api_recommend():
    """Return habitat-scored species recommendations as JSON."""
    import logging
    _rec_logger = logging.getLogger(__name__)

    data = request.get_json(force=True)
    answers = {
        "water_type": data.get("water_type", "lake"),
        "depth": data.get("depth", "moderate"),
        "flow": data.get("flow", "still"),
        "vegetation": data.get("vegetation", "moderate"),
        "clarity": data.get("clarity", "clear"),
    }

    # Geographic filtering via state code or lat/lng
    region = None
    state_code = data.get("state")
    if state_code:
        region = state_to_region(state_code)
        if region:
            _rec_logger.info("Resolved state=%s to region=%s", state_code, region)
        else:
            _rec_logger.warning("Could not resolve state=%s to any region", state_code)
    elif data.get("lat") is not None and data.get("lng") is not None:
        _rec_logger.warning(
            "lat/lng provided without state code; reverse geocoding not available server-side, skipping geographic filter"
        )

    categories = data.get("categories") or None
    try:
        offset = int(data.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0

    result = habitat_recommend(
        answers,
        region=region,
        categories=categories,
        offset=offset,
    )
    return jsonify(result)


@app.route("/api/search-species", methods=["POST"])
@rate_limit(60)
def api_search_species():
    """Search species by common or scientific name. Optionally region-filtered."""
    data = request.get_json(force=True)
    q = (data.get("query") or "").lower().strip()
    state = data.get("state")
    categories = data.get("categories") or None
    if len(q) < 2:
        return jsonify({"results": []})
    region = state_to_region(state) if state else None
    cat_filter = None
    if categories:
        cat_filter = {c.lower() for c in categories if c}
        if not cat_filter:
            cat_filter = None
    species = load_species()
    results = []
    for sp in species:
        if sp.get("category") == "plant":
            continue
        if cat_filter is not None and sp.get("category") not in cat_filter:
            continue
        if region:
            geo = [g.lower() for g in sp.get("geographic_range", [])]
            if region not in geo and "nationwide" not in geo:
                continue
        name = (sp.get("common_name") or "").lower()
        sci = (sp.get("scientific_name") or "").lower()
        slug = sp.get("slug", "")
        if q in name or q in sci or q in slug:
            results.append({
                "slug": slug,
                "common_name": sp["common_name"],
                "scientific_name": sp.get("scientific_name", ""),
                "category": sp.get("category", ""),
                "score": 0,
                "commonness": int(sp.get("commonness", 1)),
            })
    return jsonify({"results": results[:30]})


@app.route("/api/upload-logo", methods=["POST"])
@rate_limit(10)
def api_upload_logo():
    """Accept a logo image upload (PNG/JPEG, max 5 MB)."""
    if "logo" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["logo"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return jsonify({"error": "Only PNG and JPEG files are allowed"}), 400

    # Validate size (read into memory, check, then save)
    file_bytes = file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        return jsonify({"error": "File exceeds 5 MB limit"}), 400

    uploads_dir = Path(PROJECT_ROOT) / "output" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename).name)
    filename = f"{uuid.uuid4().hex}_{safe_name}"
    dest = uploads_dir / filename
    dest.write_bytes(file_bytes)

    logo_url = f"/image/output/uploads/{filename}"
    return jsonify({"logo_filename": filename, "logo_url": logo_url})


@app.route("/api/upload-background", methods=["POST"])
@admin_required
def api_upload_background():
    """Accept a background image upload (PNG/JPEG, min 1536x1024, max 15 MB)."""
    if "background" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["background"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return jsonify({"error": "Only PNG and JPEG files are allowed"}), 400

    file_bytes = file.read()
    if len(file_bytes) > 15 * 1024 * 1024:
        return jsonify({"error": "File exceeds 15 MB limit"}), 400

    # Validate minimum dimensions.
    import io as _io
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(_io.BytesIO(file_bytes)) as probe:
            pw, ph = probe.size
    except Exception:
        return jsonify({"error": "Could not read image"}), 400
    if pw < 1536 or ph < 1024:
        return jsonify(
            {"error": f"Image must be at least 1536x1024 (got {pw}x{ph})"}
        ), 400

    uploads_dir = Path(PROJECT_ROOT) / "output" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename).name)
    filename = f"bg_{uuid.uuid4().hex}_{safe_name}"
    dest = uploads_dir / filename
    dest.write_bytes(file_bytes)

    background_url = f"/image/output/uploads/{filename}"
    return jsonify(
        {"background_filename": filename, "background_url": background_url}
    )


@app.route("/api/list-backgrounds")
@admin_required
def api_list_backgrounds():
    """Return a list of available generated backgrounds."""
    bg_dir = Path(PROJECT_ROOT) / "output" / "backgrounds"
    if not bg_dir.exists():
        return jsonify({"backgrounds": []})
    items = []
    for p in sorted(bg_dir.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        rel = p.relative_to(Path(PROJECT_ROOT))
        items.append({
            "filename": p.name,
            "path": str(rel),
            "url": f"/image/{rel}",
            "size_mb": round(p.stat().st_size / 1024 / 1024, 1),
            "mtime_iso": datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat(),
        })
    return jsonify({"backgrounds": items[:24]})


# Map a 2-letter US state code to the geographic region tags used by
# assets/backgrounds/regions.json. A state can match multiple tags
# ("MI" -> midwest + great_lakes); priority order is most-specific first.
_STATE_REGION_TAGS: dict[str, list[str]] = {
    # Northeast
    "ME": ["northeast"], "NH": ["northeast"], "VT": ["northeast"],
    "MA": ["northeast"], "RI": ["northeast"], "CT": ["northeast"],
    "NY": ["northeast"], "NJ": ["northeast"], "PA": ["northeast"],
    # Southeast
    "VA": ["southeast", "appalachian"], "WV": ["southeast", "appalachian"],
    "NC": ["southeast", "appalachian"], "SC": ["southeast"],
    "GA": ["southeast"], "FL": ["southeast", "florida", "tropical", "wetland"],
    "AL": ["southeast"], "MS": ["southeast"], "LA": ["southeast", "bayou", "swamp"],
    "AR": ["southeast"], "TN": ["southeast", "appalachian"],
    "KY": ["southeast", "appalachian"],
    # Midwest / Great Lakes / Plains
    "OH": ["midwest", "great_lakes"], "MI": ["midwest", "great_lakes"],
    "IN": ["midwest"], "IL": ["midwest"],
    "WI": ["midwest", "great_lakes"], "MN": ["midwest", "great_lakes"],
    "IA": ["midwest", "plains", "prairie"], "MO": ["midwest"],
    "ND": ["midwest", "plains", "prairie"], "SD": ["midwest", "plains", "prairie"],
    "NE": ["midwest", "plains", "prairie"], "KS": ["midwest", "plains", "prairie"],
    # Southwest / Texas / Mountain West
    "TX": ["southwest", "texas", "hill_country"], "OK": ["southwest", "plains"],
    "NM": ["southwest", "desert"], "AZ": ["southwest", "desert"],
    "MT": ["mountain_west", "alpine"], "WY": ["mountain_west", "alpine"],
    "CO": ["mountain_west", "alpine"], "UT": ["mountain_west", "desert"],
    "ID": ["mountain_west"], "NV": ["mountain_west", "desert"],
    # Pacific / California / Alaska / Hawaii
    "WA": ["pacific_northwest", "coast"], "OR": ["pacific_northwest", "coast"],
    "CA": ["california", "coast"], "AK": ["alaska"], "HI": ["tropical", "coast"],
    # Mid-Atlantic
    "DE": ["northeast"], "MD": ["northeast"], "DC": ["northeast"],
}


def _load_background_regions() -> dict[str, list[str]]:
    """Load assets/backgrounds/regions.json: filename -> list[tag]."""
    regions_file = Path(PROJECT_ROOT) / "assets" / "backgrounds" / "regions.json"
    if not regions_file.exists():
        return {}
    try:
        with open(regions_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/api/public-backgrounds")
@rate_limit(60)
def api_public_backgrounds():
    """List server-curated background images for the public editor.

    Pulls from two sources:
      1) output/backgrounds/ — runtime-generated (mounted from a volume in
         production); supports PNG and JPEG.
      2) assets/backgrounds/ — repo-shipped seed library so the gallery is
         never empty even before any backgrounds have been uploaded.
    De-duplicates by filename (output/ wins on collision).

    Optional query: ?state=<two-letter-code>. When provided, each
    background's regions.json tags are scored against the state's
    region tags and results are sorted by relevance (matching first).
    """
    state_code = (request.args.get("state") or "").strip().upper()
    region_tags = _STATE_REGION_TAGS.get(state_code, []) if state_code else []
    bg_regions = _load_background_regions()

    items = []
    seen: set[str] = set()

    runtime_dir = Path(PROJECT_ROOT) / "output" / "backgrounds"
    if runtime_dir.exists():
        runtime_files = list(runtime_dir.glob("*.png")) + list(runtime_dir.glob("*.jpg")) + list(runtime_dir.glob("*.jpeg"))
        for p in sorted(runtime_files, key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name in seen:
                continue
            seen.add(p.name)
            rel = p.relative_to(Path(PROJECT_ROOT))
            items.append({
                "filename": p.name,
                "url": f"/image/{rel}",
                "regions": bg_regions.get(p.name, []),
            })

    seed_dir = Path(PROJECT_ROOT) / "assets" / "backgrounds"
    if seed_dir.exists():
        seed_files = list(seed_dir.glob("*.png")) + list(seed_dir.glob("*.jpg")) + list(seed_dir.glob("*.jpeg"))
        for p in sorted(seed_files, key=lambda x: x.name):
            if p.name in seen:
                continue
            seen.add(p.name)
            rel = p.relative_to(Path(PROJECT_ROOT))
            items.append({
                "filename": p.name,
                "url": f"/image/{rel}",
                "regions": bg_regions.get(p.name, []),
            })

    if region_tags:
        # Score each background by intersection size with the state's
        # region tags; preserve original order within equal-score buckets.
        def _score(item: dict) -> int:
            return sum(1 for t in item.get("regions", []) if t in region_tags)
        scored = list(enumerate(items))
        scored.sort(key=lambda pair: (-_score(pair[1]), pair[0]))
        items = [it for _, it in scored]

    return jsonify({"backgrounds": items[:50]})


@app.route("/api/generate-background", methods=["POST"])
@admin_required
def api_generate_background():
    """Trigger a background image generation via Replicate."""
    from webapp.background_generator import PRESET_LANDSCAPES, generate_landscape
    data = request.get_json(force=True)
    preset = data.get("preset")
    custom_prompt = data.get("prompt")
    aspect_ratio = data.get("aspect_ratio", "3:2")

    if preset and preset in PRESET_LANDSCAPES:
        prompt = PRESET_LANDSCAPES[preset]
    elif custom_prompt:
        prompt = custom_prompt.strip()
    else:
        return jsonify({"error": "Must provide 'preset' or 'prompt'"}), 400

    try:
        final_path = generate_landscape(prompt, aspect_ratio=aspect_ratio)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).exception("Background generation failed")
        return jsonify({"error": str(exc)}), 500

    rel = final_path.relative_to(Path(PROJECT_ROOT))
    return jsonify({
        "filename": final_path.name,
        "path": str(rel),
        "url": f"/image/{rel}",
    })


@app.route("/api/background-presets")
@admin_required
def api_background_presets():
    """Return the list of named preset landscapes."""
    from webapp.background_generator import PRESET_LANDSCAPES
    return jsonify({"presets": list(PRESET_LANDSCAPES.keys())})


# Phase 5a — Flask-Limiter renders. The legacy in-memory @rate_limit(20)
# above stays as a per-process belt-and-braces guard; the Flask-Limiter
# decorator below is the cross-replica enforced limit.
from review_app.limits import render_limit as _phase5a_render_limit


@app.route("/api/generate-poster", methods=["POST"])
@_phase5a_render_limit()
@rate_limit(20)
def api_generate_poster():
    """Generate a poster from selected species and options."""
    data = request.get_json(force=True)
    species_slugs = data.get("species_slugs", [])
    style_slug = data.get("style", "scientific")
    title = data.get("title", "Wildlife Poster")
    subtitle = data.get("subtitle", "") or None
    background = data.get("background", "#FFFFFF")
    logo_filename = data.get("logo_filename")
    logo_config = data.get("logo_config", {}) or {}
    background_image_filename = data.get("background_image_filename")
    # Reference-aesthetic toggles (Tasks A, B, C, D). Defaults match the
    # new poster look: portrait orientation, walnut frame baked in, common
    # name only, "FISH OF" preheader.
    orientation = (data.get("orientation") or "portrait").lower()
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"
    frame_style = data.get("frame_style") or None
    if frame_style and frame_style not in ("walnut", "oak", "black", "white", "pine"):
        frame_style = None
    show_scientific_names = bool(data.get("show_scientific_names", False))
    preheader_text = (data.get("preheader_text") or "FISH OF").upper()
    layout_style = _resolve_layout_style(data.get("layout_style"))

    if not species_slugs:
        return jsonify({"error": "No species selected"}), 400

    # Load species records and build SpeciesRef objects
    all_species = load_species()
    species_by_slug = {sp["slug"]: sp for sp in all_species}

    # Tasks C+D: scale value is now ABSOLUTE — species_scale (range 0.7-1.5)
    # IS the effective relative scale, optionally re-spread by global_size_variance.
    # We deliberately ignore the catalog's relative_scale_index here so the admin
    # slider drives sizing directly.
    variance = _global_size_variance()
    species_refs = []
    for slug in species_slugs:
        rec = species_by_slug.get(slug)
        if rec is None:
            continue
        sp_scale_raw = rec.get("species_scale")
        if sp_scale_raw is None:
            # Legacy fallback: use rsi normalized via the same tier mapping
            # we seeded the catalog with — but clamp to the new range.
            rsi = float(rec.get("relative_scale_index", 1.0) or 1.0)
            if rsi >= 2.5: sp_scale_raw = 1.5
            elif rsi >= 1.5: sp_scale_raw = 1.2
            elif rsi >= 0.7: sp_scale_raw = 1.0
            elif rsi >= 0.4: sp_scale_raw = 0.8
            else: sp_scale_raw = 0.7
        try:
            species_scale = float(sp_scale_raw)
        except (TypeError, ValueError):
            species_scale = 1.0
        species_scale = max(SPECIES_SCALE_MIN, min(SPECIES_SCALE_MAX, species_scale))
        effective = _effective_species_scale(species_scale, variance)
        species_refs.append(
            SpeciesRef(
                slug=rec["slug"],
                common_name=rec["common_name"],
                scientific_name=rec.get("scientific_name", ""),
                category=rec.get("category", ""),
                relative_scale_index=effective,
                habitat_tags=list(rec.get("habitat_tags", [])),
            )
        )

    if not species_refs:
        return jsonify({"error": "No valid species found"}), 400

    # Canvas dimensions follow orientation. 18x24" at 300 DPI = 5400x7200,
    # which matches the Prodigi 18x24 frame SKUs (3:4 aspect).
    if orientation == "portrait":
        canvas_w_default, canvas_h_default = 5400, 7200
    else:
        canvas_w_default, canvas_h_default = 7200, 5400

    # Build PosterSpec
    spec = PosterSpec(
        title=title,
        subtitle=subtitle,
        style_slug=style_slug,
        species_slugs=[ref.slug for ref in species_refs],
        layout_style=layout_style,
        canvas_width=canvas_w_default,
        canvas_height=canvas_h_default,
        background_color=background,
        show_labels=True,
    )

    # Layout
    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)

    # Filter to species that have masters
    present_refs = [ref for ref in species_refs if loader.exists(ref.slug, style_slug)]
    if not present_refs:
        return jsonify({"error": "No master images found for the selected species and style"}), 400

    # Re-build spec with only present slugs
    spec = PosterSpec(
        title=title,
        subtitle=subtitle,
        style_slug=style_slug,
        species_slugs=[ref.slug for ref in present_refs],
        layout_style=layout_style,
        canvas_width=canvas_w_default,
        canvas_height=canvas_h_default,
        background_color=background,
        show_labels=True,
    )

    engine = select_layout_engine(present_refs, spec)
    result = engine.layout(spec, present_refs, loader)

    if not result.placements:
        return jsonify({"error": "Layout produced zero placements"}), 500

    # Render
    style_profile = get_profile(layout_style)
    renderer = EditorialMultiRenderer(style_profile=style_profile)
    # Reference-aesthetic toggles
    renderer._show_scientific_names = show_scientific_names
    renderer._preheader_text = preheader_text
    renderer._frame_style = frame_style
    if logo_filename:
        logo_path = Path(PROJECT_ROOT) / "output" / "uploads" / logo_filename
        if logo_path.exists():
            renderer._logo_path = logo_path
            renderer._logo_size_pct = int(logo_config.get("size_pct", 20))
            renderer._logo_position = logo_config.get("position", "bottom-center")
            xf = logo_config.get("x_frac")
            yf = logo_config.get("y_frac")
            if xf is not None and yf is not None:
                renderer._logo_x_frac = float(xf)
                renderer._logo_y_frac = float(yf)
    if background_image_filename:
        for cand in (
            Path(PROJECT_ROOT) / "output" / "uploads" / background_image_filename,
            Path(PROJECT_ROOT) / "output" / "backgrounds" / background_image_filename,
            Path(PROJECT_ROOT) / "assets" / "backgrounds" / background_image_filename,
        ):
            if cand.exists():
                renderer._background_image_path = cand
                break

    poster_id = f"custom_{uuid.uuid4().hex}"
    posters_dir = Path(PROJECT_ROOT) / "output" / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)
    output_path = posters_dir / f"{poster_id}.png"

    try:
        renderer.render(result, output_path)
    except Exception as exc:
        return jsonify({"error": f"Render failed: {exc}"}), 500

    # Compute per-placement silhouette alpha-bbox fractions so the editor
    # can crop each master to its actual fish region (sleek species like pike
    # and gar otherwise leave large empty padding above/below the silhouette).
    from PIL import Image as _PILImage
    placements_response = []
    for placed in result.placements:
        item = {
            "slug": placed.species_ref.slug,
            "common_name": placed.species_ref.common_name,
            "scientific_name": placed.species_ref.scientific_name,
            "x": placed.x,
            "y": placed.y,
            "draw_width": placed.draw_width,
            "draw_height": placed.draw_height,
            "master_url": f"/image/output/master/{style_slug}/{placed.species_ref.slug}.png",
        }
        try:
            with _PILImage.open(placed.master.image_path) as im:
                im = im.convert("RGBA")
                alpha = im.split()[3]
                bb = alpha.getbbox()
                if bb:
                    w, h = im.size
                    item["silhouette_bbox"] = {
                        "l": bb[0] / w,
                        "t": bb[1] / h,
                        "r": bb[2] / w,
                        "b": bb[3] / h,
                    }
        except Exception:
            pass
        placements_response.append(item)

    poster_url = f"/image/output/posters/{poster_id}.png"
    return jsonify({
        "poster_url": poster_url,
        "filename": f"{poster_id}.png",
        "placements": placements_response,
        "canvas_width": spec.canvas_width,
        "canvas_height": spec.canvas_height,
        "title": title,
        "subtitle": subtitle or "",
        "background": background,
    })


@app.route("/api/render-framed-preview", methods=["POST"])
@rate_limit(20)
def api_render_framed_preview():
    """Render a free-tier preview: poster + species locks + Prodigi frame + watermark.

    Pipeline:
        layout(spec) -> renderer.render -> apply_species_locks(free_count=3)
        -> frame_wrap(finish='brown') -> apply_watermark -> JPEG on disk

    Free tier always — paid users skip the locks but still get the frame.
    The output URL is shaped like the bare-poster path so the /create page
    can drop it in without any other plumbing changes.
    """
    from PIL import Image as _PILImage

    from review_app.render.frame_compositor import DEFAULT_FINISH, frame_wrap
    from review_app.render.lock_overlay import apply_species_locks
    from review_app.render.watermark import apply_watermark

    data = request.get_json(force=True) or {}
    species_slugs = data.get("species_slugs") or []
    style_slug = data.get("style", "scientific")
    title = data.get("title", "Wildlife Poster")
    subtitle = data.get("subtitle") or None
    background = data.get("background", "#FFFFFF")
    orientation = (data.get("orientation") or "portrait").lower()
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"
    finish = (data.get("finish") or DEFAULT_FINISH).lower()
    free_count = int(data.get("free_count", 3))
    if free_count < 0:
        free_count = 0
    layout_style = _resolve_layout_style(data.get("layout_style"))

    # Server is the source of truth for unlock state — same pattern as
    # /api/render-custom.
    session_email = session.get("email") or ""
    is_unlocked = bool(session.get("unlocked")) or _is_paid(session_email)

    if not species_slugs:
        return jsonify({"error": "No species selected"}), 400

    # Build SpeciesRef list (mirrors the body of /api/generate-poster).
    all_species = load_species()
    species_by_slug = {sp["slug"]: sp for sp in all_species}
    species_refs = []
    for slug in species_slugs:
        rec = species_by_slug.get(slug)
        if rec is None:
            continue
        species_refs.append(
            SpeciesRef(
                slug=rec["slug"],
                common_name=rec["common_name"],
                scientific_name=rec.get("scientific_name", ""),
                category=rec.get("category", ""),
                relative_scale_index=float(rec.get("relative_scale_index", 1.0) or 1.0),
                habitat_tags=list(rec.get("habitat_tags", [])),
            )
        )
    if not species_refs:
        return jsonify({"error": "No valid species found"}), 400

    # 3:4 aspect (18x24") to match the Prodigi frame photos. 200 DPI is
    # plenty for the framed-preview JPEG (the frame photo is 2000x2000).
    if orientation == "portrait":
        canvas_w_default, canvas_h_default = 3600, 4800
    else:
        canvas_w_default, canvas_h_default = 4800, 3600

    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)
    present_refs = [ref for ref in species_refs if loader.exists(ref.slug, style_slug)]
    if not present_refs:
        return jsonify({"error": "No master images found for the selected species and style"}), 400

    spec = PosterSpec(
        title=title,
        subtitle=subtitle,
        style_slug=style_slug,
        species_slugs=[ref.slug for ref in present_refs],
        layout_style=layout_style,
        canvas_width=canvas_w_default,
        canvas_height=canvas_h_default,
        background_color=background,
        show_labels=True,
    )

    engine = select_layout_engine(present_refs, spec)
    result = engine.layout(spec, present_refs, loader)
    if not result.placements:
        return jsonify({"error": "Layout produced zero placements"}), 500

    renderer = EditorialMultiRenderer(style_profile=get_profile(layout_style))
    poster_id = f"framed_{uuid.uuid4().hex}"
    posters_dir = Path(PROJECT_ROOT) / "output" / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)
    raw_path = posters_dir / f"{poster_id}_raw.png"
    out_path = posters_dir / f"{poster_id}.jpg"

    try:
        renderer.render(result, raw_path)
    except Exception as exc:
        return jsonify({"error": f"Render failed: {exc}"}), 500

    try:
        with _PILImage.open(raw_path) as raw_img:
            poster = raw_img.convert("RGBA").copy()

        # Free tier: lock species 4+. Paid tier: skip the lock overlay.
        if not is_unlocked:
            poster = apply_species_locks(poster, result, free_count=free_count)

        framed = frame_wrap(poster, finish=finish)

        # Free tier: watermark. Paid tier: clean preview.
        if not is_unlocked:
            framed = apply_watermark(framed)

        framed.convert("RGB").save(out_path, "JPEG", quality=85)
    except FileNotFoundError as exc:
        return jsonify({"error": f"Frame asset missing: {exc}"}), 500
    except Exception as exc:
        return jsonify({"error": f"Composite failed: {exc}"}), 500
    finally:
        with contextlib.suppress(OSError):
            raw_path.unlink(missing_ok=True)

    poster_url = f"/image/output/posters/{poster_id}.jpg"
    return jsonify({
        "poster_url": poster_url,
        "filename": f"{poster_id}.jpg",
        "is_unlocked": is_unlocked,
        "free_count": 0 if is_unlocked else free_count,
        "finish": finish,
        "canvas_width": spec.canvas_width,
        "canvas_height": spec.canvas_height,
        "title": title,
        "subtitle": subtitle or "",
    })


@app.route("/api/render-custom", methods=["POST"])
@rate_limit(20)
def api_render_custom():
    """Render a poster with user-customized positions and text formatting."""
    data = request.get_json(force=True)
    placements_data = data.get("placements", [])
    # Orientation drives the canvas defaults. The client may also pass
    # canvas_width/canvas_height directly (legacy callers + drag-aware UI),
    # in which case those win — this preserves user-edited geometry.
    orientation = (data.get("orientation") or "").lower()
    # 18x24" at 300 DPI = 5400x7200 (3:4), matching Prodigi frame SKUs.
    if orientation == "portrait":
        canvas_w_default, canvas_h_default = 5400, 7200
    elif orientation == "landscape":
        canvas_w_default, canvas_h_default = 7200, 5400
    else:
        # Default landscape for legacy callers that don't pass orientation.
        canvas_w_default, canvas_h_default = 7200, 5400
    canvas_w = data.get("canvas_width", canvas_w_default)
    canvas_h = data.get("canvas_height", canvas_h_default)
    title = data.get("title", "")
    subtitle = data.get("subtitle", "")
    background = data.get("background", "#FFFFFF")
    style_slug = data.get("style", "scientific")
    text_config = data.get("text_config", {})
    logo_filename = data.get("logo_filename")
    bg_image_filename = data.get("background_image_filename")
    logo_config = data.get("logo_config", {})
    title_config = data.get("title_config", {}) or {}
    frame_style = data.get("frame_style") or None
    if frame_style and frame_style not in ("walnut", "oak", "black", "white", "pine"):
        frame_style = None
    show_scientific_names = bool(data.get("show_scientific_names", False))
    preheader_text = (data.get("preheader_text") or "FISH OF").upper()
    layout_style = _resolve_layout_style(data.get("layout_style"))

    # Determine paywall state. The client sends `unlocked: true|false`
    # but the server is the source of truth: trust the session (and the
    # `paid` flag on the matching lead).
    client_unlocked = bool(data.get("unlocked"))
    session_email = session.get("email") or ""
    server_unlocked = bool(session.get("unlocked")) or _is_paid(session_email)
    apply_watermark = not (client_unlocked and server_unlocked)

    if not placements_data:
        return jsonify({"error": "No placements provided"}), 400

    # Free preview: cap to top-3 placements regardless of what the
    # client submits. Defense-in-depth — even if someone tampers with
    # the JS, the renderer still only draws three.
    if apply_watermark and len(placements_data) > 3:
        placements_data = placements_data[:3]

    # Build SpeciesRef + MasterImage + PlacedItem objects from the user data
    all_species = load_species()
    species_by_slug = {sp["slug"]: sp for sp in all_species}
    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)

    placed_items = []
    for p in placements_data:
        slug = p.get("slug", "")
        rec = species_by_slug.get(slug)
        if not rec:
            continue
        if not loader.exists(slug, style_slug):
            continue
        master = loader.get(slug, style_slug)
        sp_ref = SpeciesRef(
            slug=slug,
            common_name=rec.get("common_name", slug),
            scientific_name=rec.get("scientific_name", ""),
            category=rec.get("category", ""),
            relative_scale_index=float(rec.get("relative_scale_index", 1.0)),
            habitat_tags=list(rec.get("habitat_tags", [])),
        )
        placed_items.append(PlacedItem(
            species_ref=sp_ref,
            master=master,
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            draw_width=int(p.get("draw_width", 200)),
            draw_height=int(p.get("draw_height", 150)),
        ))

    if not placed_items:
        return jsonify({"error": "No valid placements"}), 400

    spec = PosterSpec(
        title=title,
        subtitle=subtitle or None,
        style_slug=style_slug,
        species_slugs=[p.species_ref.slug for p in placed_items],
        layout_style=layout_style,
        canvas_width=canvas_w,
        canvas_height=canvas_h,
        background_color=background,
        show_labels=True,
    )

    result = LayoutResult(poster=spec, placements=placed_items, warnings=[])

    # Create renderer with custom text config
    title_size = int(text_config.get("title_size", 150))
    label_size = int(text_config.get("label_size", 42))
    label_gap = int(text_config.get("label_gap", 20))
    renderer = EditorialMultiRenderer(
        title_font_size=title_size,
        scientific_font_size=max(24, int(title_size * 0.35)),
        label_common_font_size=label_size,
        label_scientific_font_size=max(10, int(label_size * 0.76)),
        label_gap_px=label_gap,
        style_profile=get_profile(layout_style),
    )

    # Resolve custom fonts (if user picked one). Map (bold, italic) -> style suffix.
    def _style_suffix(bold: bool, italic: bool) -> str:
        if bold and italic:
            return "BoldItalic"
        if bold:
            return "Bold"
        if italic:
            return "Italic"
        return "Regular"

    fonts_dir = Path(PROJECT_ROOT) / "assets" / "fonts"
    title_family = text_config.get("title_font", "Playfair Display")
    label_family = text_config.get("label_font", "Playfair Display")
    title_suffix = _style_suffix(
        bool(text_config.get("title_bold")),
        bool(text_config.get("title_italic")),
    )
    label_suffix = _style_suffix(
        bool(text_config.get("label_bold")),
        bool(text_config.get("label_italic")),
    )
    title_font_path = fonts_dir / f"{title_family.replace(' ', '')}-{title_suffix}.ttf"
    label_font_path = fonts_dir / f"{label_family.replace(' ', '')}-{label_suffix}.ttf"
    if not title_font_path.exists():
        title_font_path = fonts_dir / f"{title_family.replace(' ', '')}-Regular.ttf"
    if not label_font_path.exists():
        label_font_path = fonts_dir / f"{label_family.replace(' ', '')}-Regular.ttf"
    if title_font_path.exists():
        renderer._custom_title_font_path = title_font_path
    if label_font_path.exists():
        renderer._custom_label_font_path = label_font_path

    # Explicit colors — bypass the adaptive palette so user edits stick.
    if text_config.get("title_color"):
        renderer.title_color = text_config["title_color"]
        renderer.scientific_color = text_config["title_color"]
    if text_config.get("label_color"):
        renderer._label_override_color = text_config["label_color"]
    renderer._disable_adaptive_palette = True

    # Optional label outline / stroke (Pillow draw.text stroke_*).
    label_stroke = text_config.get("label_stroke") or {}
    if label_stroke.get("enabled"):
        try:
            sw = int(label_stroke.get("width", 2))
        except (TypeError, ValueError):
            sw = 2
        sw = max(0, min(16, sw))
        renderer._label_stroke_width = sw
        renderer._label_stroke_fill = label_stroke.get("color", "#ffffff")
    else:
        renderer._label_stroke_width = 0
        renderer._label_stroke_fill = None

    # Disable leader lines for custom layout (user positioned manually)
    renderer.leader_line_labels = False

    # Reference-aesthetic toggles
    renderer._show_scientific_names = show_scientific_names
    renderer._preheader_text = preheader_text
    renderer._frame_style = frame_style

    # Handle logo
    if logo_filename:
        logo_path = Path(PROJECT_ROOT) / "output" / "uploads" / logo_filename
        if logo_path.exists():
            renderer._logo_path = logo_path

    # Apply logo size + position config (used by EditorialMultiRenderer logo block)
    renderer._logo_size_pct = int(logo_config.get("size_pct", 20))
    renderer._logo_position = logo_config.get("position", "bottom-center")
    _xf = logo_config.get("x_frac")
    _yf = logo_config.get("y_frac")
    if _xf is not None and _yf is not None:
        renderer._logo_x_frac = float(_xf)
        renderer._logo_y_frac = float(_yf)

    # Apply optional dragged title position. (subtitle stays grouped with title.)
    _txf = title_config.get("x_frac")
    _tyf = title_config.get("y_frac")
    if _txf is not None and _tyf is not None:
        renderer._title_x_frac = float(_txf)
        renderer._title_y_frac = float(_tyf)

    # Handle background image — try uploads first, then server-curated
    # backgrounds (runtime-generated, then repo-shipped seed library).
    if bg_image_filename:
        candidates = [
            Path(PROJECT_ROOT) / "output" / "uploads" / bg_image_filename,
            Path(PROJECT_ROOT) / "output" / "backgrounds" / bg_image_filename,
            Path(PROJECT_ROOT) / "assets" / "backgrounds" / bg_image_filename,
        ]
        for cand in candidates:
            if cand.exists():
                renderer._background_image_path = cand
                break

    # Server-side watermark for the free preview. Draws a semi-transparent
    # diagonal "PREVIEW" string across the rendered PNG when not unlocked.
    renderer._draw_watermark = apply_watermark
    renderer._watermark_text = "PREVIEW — www.fishingposter.com"

    poster_id = f"custom_{uuid.uuid4().hex}"
    posters_dir = Path(PROJECT_ROOT) / "output" / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)
    output_path = posters_dir / f"{poster_id}.png"

    try:
        renderer.render(result, output_path)
    except Exception as exc:
        return jsonify({"error": f"Render failed: {exc}"}), 500

    return jsonify({
        "poster_url": f"/image/output/posters/{poster_id}.png",
        "filename": f"{poster_id}.png",
    })


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------


# Phase 4a: the canonical /admin route now lives on the admin blueprint at
# review_app.admin (see _init_admin(app) above). The new shell renders
# Catalog/Species/Backgrounds/Sizing/etc. with role gating + sidebar.
#
# We keep the legacy JSON endpoints (/admin/data, /admin/species/<slug>/scale,
# /admin/settings/global_size_variance) in this file because the migrated
# species.html / sizing.html templates still call them. They're identical to
# what the legacy /admin page used.


@app.route("/admin/data")
@admin_required
def admin_data():
    """JSON endpoint returning species catalog with master image status."""
    species = load_species()
    master_dir = Path(MASTER_DIR)
    styles = ["scientific", "watercolor", "vintage_engraving"]

    species_data = []
    by_category: dict[str, int] = {}
    with_all = 0
    with_some = 0
    with_none = 0

    for sp in species:
        slug = sp.get("slug", "")
        category = sp.get("category", "other")
        by_category[category] = by_category.get(category, 0) + 1

        has_master = {}
        master_count = 0
        for style in styles:
            exists = (master_dir / style / f"{slug}.png").exists()
            has_master[style] = exists
            if exists:
                master_count += 1

        if master_count == len(styles):
            with_all += 1
        elif master_count > 0:
            with_some += 1
        else:
            with_none += 1

        # Task C: species_scale is the absolute size weight (0.7-1.5).
        sp_scale = sp.get("species_scale")
        if sp_scale is None:
            # Fallback if a legacy entry is missing the field.
            sp_scale = 1.0
        try:
            sp_scale = float(sp_scale)
        except (TypeError, ValueError):
            sp_scale = 1.0
        sp_scale = max(SPECIES_SCALE_MIN, min(SPECIES_SCALE_MAX, sp_scale))
        species_data.append({
            "slug": slug,
            "common_name": sp.get("common_name", slug),
            "scientific_name": sp.get("scientific_name", ""),
            "category": category,
            "geographic_range": sp.get("geographic_range", []),
            "relative_scale_index": sp.get("relative_scale_index", 1.0),
            "species_scale": sp_scale,
            "has_master": has_master,
        })

    # Catalog completeness check: flag regions with zero species in any
    # major category. This catches the "no alligators for Florida" class
    # of gap — where a whole animal group is missing from a region.
    try:
        regions_data = json.load(
            open(Path(PROJECT_ROOT) / "data" / "regions.json")
        )
    except Exception:
        regions_data = {}

    major_categories = {"fish", "bird", "turtle", "reptile", "amphibian", "mammal"}
    coverage_gaps: list[str] = []
    for region_slug in regions_data:
        cats_in_region: set[str] = set()
        for sp in species:
            geo = sp.get("geographic_range", [])
            cat = sp.get("category", "")
            if cat in major_categories and (
                region_slug in geo or "nationwide" in geo
            ):
                cats_in_region.add(cat)
        missing = major_categories - cats_in_region
        if missing:
            coverage_gaps.append(
                f"{region_slug}: missing {', '.join(sorted(missing))}"
            )

    return jsonify({
        "species": species_data,
        "summary": {
            "total": len(species),
            "by_category": by_category,
            "with_all_masters": with_all,
            "with_some_masters": with_some,
            "with_no_masters": with_none,
        },
        "settings": {
            "global_size_variance": _global_size_variance(),
            "species_scale_min": SPECIES_SCALE_MIN,
            "species_scale_max": SPECIES_SCALE_MAX,
            "global_variance_min": GLOBAL_VARIANCE_MIN,
            "global_variance_max": GLOBAL_VARIANCE_MAX,
        },
        "coverage_gaps": coverage_gaps,
    })


@app.route("/admin/species/<slug>/scale", methods=["POST"])
@admin_required
def admin_set_species_scale(slug: str):
    """Set per-species absolute scale (Task C). Persisted to volume-mounted JSON
    under the new ``species_scale`` key, range 0.7-1.5."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        scale = float(data.get("scale", 1.0))
    except (TypeError, ValueError):
        return jsonify({"error": "scale must be a number"}), 400
    if not (SPECIES_SCALE_MIN <= scale <= SPECIES_SCALE_MAX):
        return jsonify({
            "error": f"scale must be between {SPECIES_SCALE_MIN} and {SPECIES_SCALE_MAX}"
        }), 400

    # Verify slug exists in the base catalog and look up its catalog default.
    base = _load_json(SPECIES_JSON)
    base_rec = next(
        (sp for sp in base if isinstance(sp, dict) and sp.get("slug") == slug),
        None,
    )
    if base_rec is None:
        return jsonify({"error": f"unknown species slug: {slug}"}), 404

    base_default = base_rec.get("species_scale", 1.0)
    try:
        base_default = float(base_default)
    except (TypeError, ValueError):
        base_default = 1.0

    overrides = _load_species_overrides()
    entry = dict(overrides.get(slug) or {})
    # Drop any legacy field
    entry.pop("scale_override", None)
    if abs(scale - base_default) < 1e-6:
        # User-set value matches the catalog default — no override needed.
        entry.pop("species_scale", None)
    else:
        entry["species_scale"] = round(scale, 4)
    if entry:
        overrides[slug] = entry
    else:
        overrides.pop(slug, None)
    _save_species_overrides(overrides)
    return jsonify({"slug": slug, "species_scale": entry.get("species_scale", base_default)})


@app.route("/admin/settings/global_size_variance", methods=["POST"])
@admin_required
def admin_set_global_size_variance():
    """Set the global species-size variance multiplier. Range 0.0-2.0.

    Effect on each species' effective scale (Task D):
      effective_scale = 1.0 + (species_scale - 1.0) * global_size_variance

    1.0 = scales work as authored. 0.0 = uniform sizing. 2.0 = doubled spread.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        v = float(data.get("global_size_variance", 1.0))
    except (TypeError, ValueError):
        return jsonify({"error": "global_size_variance must be a number"}), 400
    if not (GLOBAL_VARIANCE_MIN <= v <= GLOBAL_VARIANCE_MAX):
        return jsonify({
            "error": (
                f"global_size_variance must be between "
                f"{GLOBAL_VARIANCE_MIN} and {GLOBAL_VARIANCE_MAX}"
            )
        }), 400
    settings = _load_admin_settings()
    settings["global_size_variance"] = round(v, 4)
    _save_admin_settings(settings)
    return jsonify({"global_size_variance": settings["global_size_variance"]})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()
