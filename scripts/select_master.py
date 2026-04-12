"""Select and publish master images for each (species, style) pair.

Exactly one variation per (species_slug, style_slug) should be flagged as
``selected_as_master`` at any given time. This module exposes both a CLI and
importable helpers for managing that state and for copying selected images
into ``output/master/{style_slug}/{species_slug}.png`` - the canonical
location consumed by downstream tools (poster layout, export, review app).

CLI examples::

    # Mark variation 2 of red-fox / vintage-naturalist as the master.
    python -m scripts.select_master \
        --species red-fox --style vintage-naturalist --variation 2

    # Copy every currently selected master into output/master/.
    python -m scripts.select_master --copy

    # Print the current selections.
    python -m scripts.select_master --list
"""
from __future__ import annotations


from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path
from typing import Any

from config.settings import MASTER_DIR, PROJECT_ROOT
from scripts.build_manifest import load_manifest, save_manifest

logger = logging.getLogger(__name__)


def _resolve(path_str: str) -> Path:
    """Resolve a possibly-relative path against ``PROJECT_ROOT``."""
    p = Path(path_str)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def mark_selected(species_slug: str, style_slug: str, variation: int) -> dict[str, Any]:
    """Mark one variation as the master for its (species, style) pair.

    Sets ``selected_as_master=True`` on the chosen record and False on all
    other records with the same ``(species_slug, style_slug)``. Saves the
    manifest and returns the selected record.

    Raises ``ValueError`` if no matching record exists.
    """
    records = load_manifest()
    target: dict[str, Any] | None = None
    pair_records: list[dict[str, Any]] = []

    for rec in records:
        if (
            rec.get("species_slug") == species_slug
            and rec.get("style_slug") == style_slug
        ):
            pair_records.append(rec)
            if int(rec.get("variation", -1)) == int(variation):
                target = rec

    if target is None:
        raise ValueError(
            f"No manifest record for species={species_slug!r} "
            f"style={style_slug!r} variation={variation}"
        )

    for rec in pair_records:
        rec["selected_as_master"] = rec is target

    save_manifest(records)
    logger.info(
        "Marked %s/%s v%d as master (unselected %d sibling(s))",
        style_slug,
        species_slug,
        variation,
        len(pair_records) - 1,
    )
    return target


def list_selections() -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{style_slug: {species_slug: record}}`` for currently selected masters."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for rec in load_manifest():
        if not rec.get("selected_as_master"):
            continue
        style = rec.get("style_slug")
        species = rec.get("species_slug")
        if not style or not species:
            continue
        out.setdefault(style, {})[species] = rec
    return out


def _master_out_path(style_slug: str, species_slug: str) -> Path:
    return MASTER_DIR / style_slug / f"{species_slug}.png"


def copy_masters() -> tuple[int, int]:
    """Copy every selected master to ``output/master/``.

    Prefers ``normalized_path`` and falls back to ``raw_path`` if no
    normalized output exists yet. Returns ``(copied, failed)``.
    """
    copied = failed = 0
    for rec in load_manifest():
        if not rec.get("selected_as_master"):
            continue

        style = rec.get("style_slug")
        species = rec.get("species_slug")
        if not style or not species:
            logger.warning("Selected record missing species/style: %r", rec)
            failed += 1
            continue

        src_str = rec.get("normalized_path") or rec.get("raw_path")
        if not src_str:
            logger.error(
                "Selected record %s/%s has no normalized_path or raw_path",
                style,
                species,
            )
            failed += 1
            continue

        src = _resolve(src_str)
        if not src.exists():
            logger.error("Source file does not exist for %s/%s: %s", style, species, src)
            failed += 1
            continue

        dest = _master_out_path(style, species)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.error("Copy failed %s -> %s: %s", src, dest, exc)
            failed += 1
            continue

        source_kind = "normalized" if rec.get("normalized_path") else "raw"
        logger.info("Copied %s (%s) -> %s", src.name, source_kind, dest)
        copied += 1

    logger.info("copy_masters done. copied=%d failed=%d", copied, failed)
    return (copied, failed)


def _print_selections_table() -> None:
    selections = list_selections()
    if not selections:
        print("No masters currently selected.")
        return

    rows: list[tuple[str, str, str, str]] = []
    for style in sorted(selections):
        for species in sorted(selections[style]):
            rec = selections[style][species]
            src = rec.get("normalized_path") or rec.get("raw_path") or "-"
            rows.append((style, species, f"v{rec.get('variation', '?')}", src))

    headers = ("STYLE", "SPECIES", "VARIATION", "SOURCE")
    widths = [
        max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)
    ]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark and publish master images for wildprint."
    )
    parser.add_argument("--species", type=str, default=None, help="Species slug")
    parser.add_argument("--style", type=str, default=None, help="Style slug")
    parser.add_argument("--variation", type=int, default=None, help="Variation index")
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy all selected masters into output/master/.",
    )
    parser.add_argument(
        "--list",
        dest="list_",
        action="store_true",
        help="Print current master selections.",
    )
    parser.add_argument(
        "--all-styles",
        action="store_true",
        help="(Default with --copy) Copy masters for all styles.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    did_something = False

    if args.species and args.style and args.variation is not None:
        try:
            mark_selected(args.species, args.style, args.variation)
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
        did_something = True

    if args.copy:
        copy_masters()
        did_something = True

    if args.list_:
        _print_selections_table()
        did_something = True

    if not did_something:
        # Default behavior with no flags: show current selections.
        _print_selections_table()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
