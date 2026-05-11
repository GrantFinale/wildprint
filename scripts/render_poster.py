"""Render a wildprint poster from existing master images.

Usage
-----
::

    python -m scripts.render_poster \\
        --style scientific \\
        --title "Freshwater Fish of the Midwest" \\
        --subtitle "A field reference" \\
        --layout scaled_row \\
        --output output/posters/midwest_fish_scientific.png

Logic
-----
1. Load species from ``SPECIES_JSON``, filter to enabled species.
2. Optionally narrow to ``--species slug1,slug2``.
3. Build ``SpeciesRef`` / ``StyleRef`` objects, construct a ``PosterSpec``.
4. Resolve masters via ``FileSystemMasterImageLoader``; skip (warn) on miss.
5. Select layout engine (``scaled_row`` default, or ``grid``).
6. Print any warnings returned by the engine.
7. Render to disk with ``PillowPosterRenderer``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from config.settings import MASTER_DIR, OUTPUT_DIR, SPECIES_JSON, STYLES_JSON
from poster_layout import (
    EditorialMultiRenderer,
    EditorialPosterRenderer,
    FileSystemMasterImageLoader,
    GridLayoutEngine,
    HeroLayoutEngine,
    PackedLayoutEngine,
    PillowPosterRenderer,
    PosterSpec,
    ScaledRowLayoutEngine,
    SmallEnsembleLayoutEngine,
    SpeciesRef,
    StyleRef,
    select_layout_engine,
)

logger = logging.getLogger(__name__)


# --- CLI plumbing ------------------------------------------------------------


def _parse_canvas(value: str) -> tuple[int, int]:
    try:
        w_str, h_str = value.lower().split("x")
        return int(w_str), int(h_str)
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(
            f"Invalid --canvas '{value}'. Expected format WxH (e.g. 5100x3300)."
        ) from exc


# Canvas presets: preset slug → (width_px, height_px).
CANVAS_PRESETS: dict[str, tuple[int, int]] = {
    "tabloid_landscape": (5100, 3300),
    "tabloid_portrait": (3300, 5100),
    "poster_24x36": (7200, 10800),
    "poster_36x24": (10800, 7200),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a wildprint poster from existing master images.",
    )
    parser.add_argument("--style", required=True, help="Style slug to pull masters from.")
    parser.add_argument(
        "--title",
        default=None,
        help="Poster title. Required unless --list-missing is set.",
    )
    parser.add_argument("--subtitle", default=None, help="Optional subtitle.")
    parser.add_argument(
        "--layout",
        choices=("scaled_row", "grid"),
        default="scaled_row",
        help="Layout algorithm to use (default: scaled_row).",
    )
    parser.add_argument(
        "--species",
        default=None,
        help="Comma-separated species slugs (default: all enabled).",
    )
    parser.add_argument(
        "--category",
        default=None,
        help=(
            "Filter enabled species to a single category slug (e.g. 'fish', "
            "'bird', 'turtle'). Applied before --species so both can combine."
        ),
    )
    parser.add_argument(
        "--group-by",
        choices=("none", "habitat", "scientific_family"),
        default="none",
        help=(
            "Sort species before layout so related ones sit adjacent. "
            "'habitat' groups by the first habitat tag; 'scientific_family' "
            "groups by the genus (first word of scientific_name). "
            "Default: none (preserve species.json order)."
        ),
    )
    parser.add_argument(
        "--canvas",
        type=_parse_canvas,
        default=(5100, 3300),
        help="Canvas size as WxH in pixels (default: 5100x3300 landscape 17x11 @ 300 DPI).",
    )
    parser.add_argument(
        "--canvas-preset",
        choices=tuple(CANVAS_PRESETS.keys()),
        default=None,
        help=(
            "Named canvas preset. Choices: tabloid_landscape (5100x3300), "
            "tabloid_portrait (3300x5100), poster_24x36 (7200x10800), "
            "poster_36x24 (10800x7200). "
            "If both --canvas and --canvas-preset are given, --canvas-preset wins."
        ),
    )
    parser.add_argument(
        "--max-per-row-fraction",
        type=float,
        default=None,
        help=(
            "Override ScaledRowLayoutEngine.max_per_row_fraction. Lower → "
            "smaller per-item width → more items per row. Ignored for the "
            "grid engine. Default: use engine default."
        ),
    )
    parser.add_argument(
        "--background",
        default=None,
        help=(
            "Poster background color as a hex string (e.g. '#FFFFFF', "
            "'#F5EFE0', '#12263A'). Honored by every renderer. Default: "
            "#FFFFFF for both editorial and multi-species posters."
        ),
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help="Do not render species labels beneath illustrations.",
    )
    parser.add_argument(
        "--inline-labels",
        action="store_true",
        help=(
            "Use inline labels below each species (legacy). Default is "
            "leader-line labels placed in whitespace."
        ),
    )
    parser.add_argument(
        "--background-image",
        default=None,
        help=(
            "Path to a background image (PNG/JPEG). When set, the poster "
            "renders onto this image instead of a solid color. Applies only "
            "to the editorial multi-species renderer."
        ),
    )
    parser.add_argument(
        "--logo",
        default=None,
        help=(
            "Path to a logo image (PNG with alpha or JPEG). Composited "
            "centered in the bottom caption band. Intended for bulk-purchase "
            "buyers who want their brand on the poster."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output PNG path (default: output/posters/{style}_poster.png).",
    )
    parser.add_argument(
        "--list-missing",
        action="store_true",
        help="Instead of rendering, print species missing masters for the style.",
    )
    parser.add_argument(
        "--editorial",
        action="store_true",
        help=(
            "Render an editorial-grade poster with Didot typography. For "
            "N=1 species this routes to HeroLayoutEngine + "
            "EditorialPosterRenderer; for N=2..8 it routes to "
            "SmallEnsembleLayoutEngine + EditorialMultiRenderer. N>=9 is "
            "Phase 2 and is rejected. Defaults the canvas to "
            "tabloid_portrait (3300x5100) when no canvas/preset is "
            "supplied, and rejects explicit --layout grid/scaled_row."
        ),
    )
    return parser


# --- Data loading ------------------------------------------------------------


def _load_species_records() -> list[dict]:
    with Path(SPECIES_JSON).open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_style_records() -> list[dict]:
    with Path(STYLES_JSON).open("r", encoding="utf-8") as f:
        return json.load(f)


def _to_species_ref(rec: dict) -> SpeciesRef:
    return SpeciesRef(
        slug=rec["slug"],
        common_name=rec["common_name"],
        scientific_name=rec.get("scientific_name", ""),
        category=rec.get("category", ""),
        relative_scale_index=float(rec.get("relative_scale_index", 1.0)),
        habitat_tags=list(rec.get("habitat_tags", [])),
        water_column=rec.get("water_column", "mid"),
    )


def _to_style_ref(rec: dict) -> StyleRef:
    return StyleRef(
        slug=rec["slug"],
        style_name=rec["style_name"],
        description=rec.get("description", ""),
    )


def _layout_explicitly_set(argv: list[str] | None) -> bool:
    """Return True if ``--layout`` was explicitly passed on the CLI.

    argparse can't distinguish "user passed the default" from "user didn't
    pass anything," so we peek at raw argv. ``None`` means argparse was
    called with sys.argv, so fall back to ``sys.argv``.
    """
    tokens = argv if argv is not None else sys.argv[1:]
    return any(t == "--layout" or t.startswith("--layout=") for t in tokens)


# --- Main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.list_missing and not args.title:
        parser.error("--title is required unless --list-missing is set")

    # --- Editorial flag validation (happens before heavy lifting).
    if args.editorial:
        if args.layout in ("grid", "scaled_row") and _layout_explicitly_set(argv):
            parser.error(
                "--editorial cannot be combined with --layout grid/scaled_row; "
                "editorial mode picks its own engine via select_layout_engine."
            )

    style_slug: str = args.style
    # --canvas-preset wins over --canvas when both are supplied.
    if args.canvas_preset is not None:
        canvas_w, canvas_h = CANVAS_PRESETS[args.canvas_preset]
    elif args.editorial and args.canvas == (5100, 3300):
        # User did not explicitly choose a canvas. For N=1 (hero), portrait
        # is best. For N≥2 (multi-species), landscape fits horizontal fish
        # better. We don't know N yet (species filtering happens below), so
        # we'll fix this up after filtering. For now, store the sentinel.
        canvas_w, canvas_h = 0, 0  # sentinel: resolve after species filtering
        logger.info("Editorial mode: canvas will auto-select based on species count.")
    else:
        canvas_w, canvas_h = args.canvas

    # --- Load species / styles
    all_species = _load_species_records()
    enabled = [rec for rec in all_species if rec.get("enabled", True)]

    if args.category:
        wanted_cat = args.category.strip()
        enabled = [rec for rec in enabled if rec.get("category", "") == wanted_cat]
        if not enabled:
            logger.error("No enabled species matched --category=%s", args.category)
            return 2

    if args.species:
        wanted = {s.strip() for s in args.species.split(",") if s.strip()}
        enabled = [rec for rec in enabled if rec["slug"] in wanted]
        if not enabled:
            logger.error("No enabled species matched --species=%s", args.species)
            return 2

    styles = _load_style_records()
    style_rec = next((s for s in styles if s["slug"] == style_slug), None)
    if style_rec is None:
        logger.error("Style slug '%s' not found in %s", style_slug, STYLES_JSON)
        return 2

    style_ref = _to_style_ref(style_rec)  # noqa: F841 - kept for future use / parity

    # --- Loader and missing-master filter
    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)

    if args.list_missing:
        missing = [
            rec["slug"]
            for rec in enabled
            if not loader.exists(rec["slug"], style_slug)
        ]
        if not missing:
            print(f"All {len(enabled)} enabled species have masters for '{style_slug}'.")
        else:
            print(f"Missing masters for style '{style_slug}':")
            for slug in missing:
                print(f"  - {slug}")
        return 0

    present_records = [
        rec for rec in enabled if loader.exists(rec["slug"], style_slug)
    ]
    missing_records = [
        rec for rec in enabled if not loader.exists(rec["slug"], style_slug)
    ]
    for rec in missing_records:
        logger.warning(
            "Skipping '%s' — no master for style '%s'.", rec["slug"], style_slug
        )

    if not present_records:
        logger.error(
            "No species with existing masters for style '%s'; nothing to render.",
            style_slug,
        )
        return 1

    species_refs = [_to_species_ref(rec) for rec in present_records]

    # --- Group-by sort (stable; preserves prior order within a group).
    if args.group_by == "habitat":
        species_refs = sorted(
            species_refs,
            key=lambda ref: ref.habitat_tags[0] if ref.habitat_tags else "",
        )
    elif args.group_by == "scientific_family":
        species_refs = sorted(
            species_refs,
            key=lambda ref: ref.scientific_name.split(" ", 1)[0] if ref.scientific_name else "",
        )
    # "none" → leave as-is.

    # --- Resolve deferred editorial canvas (sentinel 0×0 from above).
    if canvas_w == 0 and canvas_h == 0:
        if len(species_refs) <= 1:
            canvas_w, canvas_h = CANVAS_PRESETS["tabloid_portrait"]
            logger.info("Editorial hero (N=1): canvas → tabloid_portrait 3300x5100.")
        else:
            canvas_w, canvas_h = CANVAS_PRESETS["tabloid_landscape"]
            logger.info(
                "Editorial packed (N=%d): canvas → tabloid_landscape 5100x3300.",
                len(species_refs),
            )

    # --- PosterSpec
    effective_layout = "hero" if args.editorial else args.layout
    # User-supplied --background wins. Otherwise default white for every
    # renderer — editorial posters no longer force ivory, the subject is
    # alpha-keyed enough in practice that any backdrop works.
    if args.background:
        bg_value = args.background.strip()
        if not bg_value.startswith("#"):
            bg_value = "#" + bg_value
        effective_bg = bg_value
    else:
        effective_bg = "#FFFFFF"
    spec = PosterSpec(
        title=args.title,
        subtitle=args.subtitle,
        style_slug=style_slug,
        species_slugs=[ref.slug for ref in species_refs],
        layout_style=effective_layout,
        canvas_width=canvas_w,
        canvas_height=canvas_h,
        background_color=effective_bg,
        show_labels=not args.no_labels,
    )

    # --- Engine
    if args.editorial:
        engine = select_layout_engine(species_refs, spec)
    elif args.layout == "grid":
        engine = GridLayoutEngine()
    else:
        if args.max_per_row_fraction is not None:
            engine = ScaledRowLayoutEngine(
                max_per_row_fraction=args.max_per_row_fraction,
            )
        else:
            engine = ScaledRowLayoutEngine()

    logger.info(
        "Laying out %d species on %dx%d canvas using %s.",
        len(species_refs),
        canvas_w,
        canvas_h,
        effective_layout,
    )
    result = engine.layout(spec, species_refs, loader)

    for w in result.warnings:
        logger.warning("%s", w)

    if not result.placements:
        logger.error("Layout produced zero placements; aborting render.")
        return 1

    # --- Output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(OUTPUT_DIR) / "posters" / f"{style_slug}_poster.png"

    renderer: PillowPosterRenderer | EditorialPosterRenderer | EditorialMultiRenderer
    if args.editorial:
        if len(species_refs) <= 1:
            renderer = EditorialPosterRenderer()
        else:
            renderer = EditorialMultiRenderer()
        # Pass logo path through for editorial renderers.
        if args.logo:
            renderer._logo_path = Path(args.logo)  # type: ignore[attr-defined]
        if args.inline_labels and isinstance(renderer, EditorialMultiRenderer):
            renderer.leader_line_labels = False
        if args.background_image:
            bg_img_path = Path(args.background_image)
            if not bg_img_path.is_file():
                parser.error(
                    f"--background-image '{bg_img_path}' does not exist"
                )
            renderer._background_image_path = bg_img_path  # type: ignore[attr-defined]
    else:
        renderer = PillowPosterRenderer()
    logger.info("Rendering poster to %s ...", output_path)
    renderer.render(result, output_path)
    logger.info("Done. Wrote %s", output_path)
    print(str(output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
