"""End-to-end pipeline: generate -> normalize -> manifest -> copy masters -> variants.

Wraps the per-stage scripts so a freshly generated species lands in
``output/master/<style>/<slug>.png`` AND ``output/master_thumbs/...`` AND
``output/master_previews/...`` in one shot — no manual chaining required.

Why this exists
---------------
The admin "Generate Selected" button used to call ``scripts.batch_generate``
only, which writes raws but not normalized + master + variants. A freshly
generated species would render in raws on disk but show as "missing" in the
species picker (which scans ``output/master/<style>/<slug>.png`` directly),
and the admin had to remember to also click Normalize + Copy Masters to
make it visible. This script runs all the stages in order and surfaces a
single non-zero exit code on the first failure.

Usage::

    python -m scripts.run_pipeline --species redear_sunfish --style vintage_engraving
    python -m scripts.run_pipeline --species bluegill                # all enabled styles
    python -m scripts.run_pipeline --variations 2 --provider openai  # full matrix

The flags mirror ``scripts.batch_generate`` for the generation stage. After
generation, normalize/manifest/select_master/build_image_variants are run
with the same ``--species``/``--style`` filters where they support them.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

logger = logging.getLogger("wildprint.run_pipeline")


def _configure_logging(verbose: bool) -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] pipeline: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def _run_stage(name: str, fn, argv: list[str]) -> int:
    """Invoke ``fn(argv)`` and log its outcome. Returns the stage's exit code.

    ``fn`` is the stage's ``main(argv)`` function. We import lazily so a
    stage that fails to import (missing optional dep, etc.) still lets the
    other stages report cleanly.
    """
    logger.info("--- stage start: %s argv=%r", name, argv)
    try:
        rc = fn(argv) or 0
    except SystemExit as exc:  # argparse may raise SystemExit on bad args
        rc = exc.code if isinstance(exc.code, int) else 1
    except Exception:
        logger.exception("--- stage crash: %s", name)
        return 1
    if rc == 0:
        logger.info("--- stage ok: %s", name)
    else:
        logger.error("--- stage failed: %s rc=%s", name, rc)
    return rc


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run the full image pipeline for one or more species: "
            "generate -> normalize -> manifest -> copy masters -> variants."
        )
    )
    # Filters (forwarded to every stage that accepts them).
    p.add_argument("--species", default=None, help="Species slug filter (single).")
    p.add_argument("--style", default=None, help="Style slug filter (single).")
    # batch_generate-only flags.
    p.add_argument("--variations", type=int, default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--size", default=None)
    p.add_argument("--quality", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--skip-generate",
        action="store_true",
        help="Skip the generation stage (useful when raws already exist and "
        "you just want to (re)normalize + copy + build variants).",
    )
    p.add_argument(
        "--force-variants",
        action="store_true",
        help="Pass --force to build_image_variants (rebuild all variants).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    # Stage 1: batch_generate ------------------------------------------------
    if not args.skip_generate:
        from scripts import batch_generate

        gen_argv: list[str] = []
        if args.species:
            gen_argv += ["--species", args.species]
        if args.style:
            gen_argv += ["--style", args.style]
        if args.variations is not None:
            gen_argv += ["--variations", str(args.variations)]
        if args.provider:
            gen_argv += ["--provider", args.provider]
        if args.model:
            gen_argv += ["--model", args.model]
        if args.size:
            gen_argv += ["--size", args.size]
        if args.quality:
            gen_argv += ["--quality", args.quality]
        if args.seed is not None:
            gen_argv += ["--seed", str(args.seed)]
        rc = _run_stage("generate", batch_generate.main, gen_argv)
        if rc != 0:
            return rc

    # Stage 2: normalize_images ---------------------------------------------
    from scripts import normalize_images

    norm_argv: list[str] = []
    if args.species:
        norm_argv += ["--species", args.species]
    if args.style:
        norm_argv += ["--style", args.style]
    rc = _run_stage("normalize", normalize_images.main, norm_argv)
    if rc != 0:
        return rc

    # Stage 3: build_manifest -----------------------------------------------
    # No filter flags — manifest rebuild is fast and global. Keeps every
    # newly created sidecar JSON wired into the manifest so select_master
    # can find it.
    from scripts import build_manifest

    rc = _run_stage("manifest", build_manifest.main, [])
    if rc != 0:
        return rc

    # Stage 3.5: auto-select a master variation for any (species, style) pair
    # that has variations but none yet selected. Without this, select_master
    # --copy is a no-op for freshly generated species — there's nothing
    # flagged as the master to copy. Picks the lowest variation number so
    # results are deterministic; the admin can override later via the
    # /review UI if they want a different variant.
    from scripts import select_master
    from scripts.build_manifest import load_manifest

    # Walk the manifest twice: once to find pairs that already have a
    # selection (skip them), once to pick the lowest variation for the rest.
    already_selected: set[tuple[str, str]] = set()
    candidate_var: dict[tuple[str, str], int] = {}
    for rec in load_manifest():
        sp = rec.get("species_slug")
        st = rec.get("style_slug")
        var = rec.get("variation")
        if not sp or not st or var is None:
            continue
        if args.species and sp != args.species:
            continue
        if args.style and st != args.style:
            continue
        key = (sp, st)
        if rec.get("selected_as_master"):
            already_selected.add(key)
            continue
        cur = candidate_var.get(key)
        if cur is None or var < cur:
            candidate_var[key] = var

    auto_selected = 0
    for key, var in candidate_var.items():
        if key in already_selected:
            continue
        sp, st = key
        try:
            select_master.mark_selected(sp, st, var)
            logger.info("auto-selected %s/%s v%d as master", sp, st, var)
            auto_selected += 1
        except Exception:
            logger.exception("auto-select failed for %s/%s v%s", sp, st, var)
    if auto_selected:
        logger.info(
            "--- stage ok: auto-select (%d (species, style) pairs)",
            auto_selected,
        )

    # Stage 4: select_master --copy -----------------------------------------
    rc = _run_stage("copy-masters", select_master.main, ["--copy"])
    if rc != 0:
        return rc

    # Stage 5: build_image_variants -----------------------------------------
    # Filters apply here too so a one-species run only builds variants for
    # that species (fast).
    from scripts import build_image_variants

    var_argv: list[str] = []
    if args.style:
        var_argv += ["--style", args.style]
    if args.species:
        var_argv += ["--slug", args.species]
    if args.force_variants:
        var_argv += ["--force"]
    rc = _run_stage("variants", build_image_variants.main, var_argv)
    if rc != 0:
        return rc

    logger.info("pipeline complete: all stages succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
