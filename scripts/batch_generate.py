"""Batch image generator.

Runs the configured provider over the filtered species x style matrix,
writing PNGs, JSON sidecars, and appending to a central manifest.

Run from project root with PYTHONPATH=. set:

    python -m scripts.batch_generate --provider mock --variations 2
"""
from __future__ import annotations


import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import (
    DEFAULT_IMAGE_SIZE,
    DEFAULT_MODEL,
    DEFAULT_NUM_VARIATIONS,
    DEFAULT_PROVIDER,
    DEFAULT_SEED,
    MANIFEST_PATH,
    METADATA_DIR,
    RAW_DIR,
)
from providers import get_provider
from scripts.generate_prompts import (
    build_prompt,
    load_species,
    load_styles,
)

logger = logging.getLogger("wildprint.batch_generate")


def _configure_logging() -> None:
    """Attach console + file handlers to the module logger (idempotent)."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    Path(METADATA_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(METADATA_DIR) / "generation.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-generate fish poster images across species x style combos."
    )
    parser.add_argument("--species", default=None, help="Filter to one species slug.")
    parser.add_argument("--style", default=None, help="Filter to one style slug.")
    parser.add_argument(
        "--variations",
        type=int,
        default=DEFAULT_NUM_VARIATIONS,
        help="Number of variations to generate per combo.",
    )
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER, help="Provider name (mock, openai)."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model identifier.")
    parser.add_argument(
        "--size", default=DEFAULT_IMAGE_SIZE, help="Image size as WxH (e.g. 1024x1024)."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Base seed; each variation uses seed + (v-1).",
    )
    parser.add_argument(
        "--quality",
        choices=("low", "medium", "high", "auto"),
        default="auto",
        help=(
            "Image quality for providers that support it. gpt-image-1: low/medium/high/auto "
            "(billed differently — low ~$0.02/img, medium ~$0.07/img, high ~$0.19/img at 1536x1024). "
            "Mock and Recraft ignore this. Default: auto."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned work without calling the provider or writing files.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all enabled combinations (default behavior if no filters).",
    )
    return parser.parse_args(argv)


def _filter_records(
    records: list[dict], slug: str | None, kind: str
) -> list[dict]:
    """Return enabled records, optionally filtered to a single slug."""
    filtered = [r for r in records if r.get("enabled", True)]
    if slug is not None:
        filtered = [r for r in filtered if r.get("slug") == slug]
        if not filtered:
            logger.warning("No enabled %s matched slug %r", kind, slug)
    return filtered


def _append_manifest(record: dict) -> None:
    """Append a record to the manifest JSON array (read-modify-write)."""
    manifest_path = Path(MANIFEST_PATH)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.warning("Manifest was not a list; resetting.")
                data = []
        except json.JSONDecodeError:
            logger.warning("Manifest was not valid JSON; resetting.")
            data = []
    else:
        data = []

    data.append(record)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _write_sidecar(path: Path, record: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for batch generation."""
    _configure_logging()
    args = _parse_args(argv)

    species_records = _filter_records(load_species(), args.species, "species")
    style_records = _filter_records(load_styles(), args.style, "style")

    if not species_records or not style_records:
        logger.error("Nothing to generate: species=%d styles=%d",
                     len(species_records), len(style_records))
        return 1

    total_combos = len(species_records) * len(style_records)
    logger.info(
        "Starting batch: provider=%s model=%s size=%s quality=%s combos=%d variations=%d dry_run=%s",
        args.provider, args.model, args.size, args.quality, total_combos, args.variations, args.dry_run,
    )

    provider_cls = get_provider(args.provider)
    provider = None
    if not args.dry_run:
        provider = provider_cls(
            model=args.model, image_size=args.size, quality=args.quality
        )

    raw_root = Path(RAW_DIR)
    successes = 0
    failures = 0

    for sp in species_records:
        for st in style_records:
            try:
                prompt = build_prompt(sp, st)
            except Exception as e:
                logger.error("Failed to build prompt for %s x %s: %s",
                             sp.get("slug"), st.get("slug"), e)
                failures += 1
                continue

            species_slug = sp["slug"]
            style_slug = st["slug"]
            combo_dir = raw_root / style_slug / species_slug

            for v in range(1, args.variations + 1):
                seed_requested = args.seed
                seed_used = None
                if seed_requested is not None:
                    seed_used = int(seed_requested) + v - 1

                basename = f"{species_slug}_{style_slug}_v{v}"
                raw_path = combo_dir / f"{basename}.png"
                sidecar_path = combo_dir / f"{basename}.json"

                log_prefix = (
                    f"{species_slug} x {style_slug} v{v} "
                    f"(seed_used={seed_used})"
                )

                if args.dry_run:
                    logger.info("[dry-run] would generate %s -> %s", log_prefix, raw_path)
                    successes += 1
                    continue

                try:
                    combo_dir.mkdir(parents=True, exist_ok=True)
                    png_bytes = provider.generate(
                        prompt, seed=seed_used, style_meta=st
                    )
                    with open(raw_path, "wb") as f:
                        f.write(png_bytes)

                    record = {
                        "species_slug": species_slug,
                        "species_common_name": sp.get("common_name"),
                        "style_slug": style_slug,
                        "style_name": st.get("style_name"),
                        "provider": args.provider,
                        "model": args.model,
                        "image_size": args.size,
                        "prompt": prompt,
                        "variation": v,
                        "seed_requested": seed_requested,
                        "seed_used": seed_used,
                        "supports_seed": bool(
                            getattr(provider_cls, "supports_seed", False)
                        ),
                        "generated_at": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "raw_path": str(raw_path),
                        "normalized_path": None,
                        "selected_as_master": False,
                    }
                    _write_sidecar(sidecar_path, record)
                    _append_manifest(record)

                    logger.info("OK %s -> %s", log_prefix, raw_path)
                    successes += 1
                except Exception as e:
                    logger.error("FAIL %s: %s", log_prefix, e)
                    failures += 1
                    continue

    logger.info("Batch complete: %d succeeded, %d failed", successes, failures)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
