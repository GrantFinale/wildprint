"""Flask review app for selecting master wildprint illustrations.

Run with:

    python -m review_app.app

Or:

    python review_app/app.py
"""
from __future__ import annotations


from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

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
    url_for,
)

from config.settings import (
    PROJECT_ROOT,
    RAW_DIR,
    NORMALIZED_DIR,
    MASTER_DIR,
    SPECIES_JSON,
    STYLES_JSON,
    MANIFEST_PATH,
)
from scripts.build_manifest import load_manifest, save_manifest, find_record
from scripts.select_master import mark_selected, copy_masters


app = Flask(__name__)
app.secret_key = os.environ.get("WILDPRINT_SECRET_KEY", "dev-only-wildprint-secret")


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_json(path) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
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


def load_species() -> list[dict]:
    return _load_json(SPECIES_JSON)


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
) -> Optional[dict]:
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
) -> Optional[dict]:
    """Return the record to use as a display thumbnail for a (species, style)."""
    selected = get_selected(records, species_slug, style_slug)
    if selected is not None:
        return selected
    variations = get_variations(records, species_slug, style_slug)
    return variations[0] if variations else None


def record_image_relpath(record: dict) -> Optional[str]:
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


def _find_style(styles: list[dict], slug: str) -> Optional[dict]:
    for s in styles:
        if s.get("slug") == slug:
            return s
    return None


def _find_species(species: list[dict], slug: str) -> Optional[dict]:
    for sp in species:
        if sp.get("slug") == slug:
            return sp
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/browse")
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truthy(val: Optional[str]) -> bool:
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
        output = (form.get("output") or "").strip()
        if output:
            argv += ["--output", _validate_output_path(output)]
        return argv

    raise ValueError(f"unknown command id: {cmd_id!r}")


def _trim_old_jobs(max_age_seconds: int = 3600) -> None:
    """Drop JOBS entries older than max_age_seconds (based on started_at)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
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
    }

    return render_template(
        "console.html",
        status=status,
        species=enabled_species,
        styles=enabled_styles,
    )


@app.route("/run/<cmd_id>", methods=["POST"])
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
        variation: Optional[int] = None
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
                    stat.st_mtime, tz=timezone.utc
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
                    stat.st_mtime, tz=timezone.utc
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
def copy_masters_route():
    try:
        copy_masters()
        flash("Master images copied successfully.", "success")
    except Exception as exc:  # pragma: no cover - defensive
        flash(f"Failed to copy masters: {exc}", "error")
    return redirect(url_for("browse"))


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


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()
