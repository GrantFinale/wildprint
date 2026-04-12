"""Build and manage the wildprint manifest.

The manifest is a JSON array at ``metadata/manifest.json`` that tracks every
generated image: its provider, prompt, filesystem paths, and master-selection
status. It is the single source of truth for the rest of the pipeline.

This module is both a runnable script (``python -m scripts.build_manifest``)
and a library imported by ``normalize_images.py``, ``select_master.py``, and
the Flask review app.

Rebuild semantics
-----------------
``rebuild_manifest()`` walks ``output/raw/**/*.json`` sidecars and constructs
a fresh list of records. It then merges the fresh list with the existing
manifest on the composite key ``(species_slug, style_slug, variation)`` so
that mutable fields populated by later stages of the pipeline -
``selected_as_master`` and ``normalized_path`` - are preserved across
rebuilds.
"""
from __future__ import annotations


from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from config.settings import MANIFEST_PATH, METADATA_DIR, RAW_DIR

logger = logging.getLogger(__name__)

# Fields that describe the generation itself and should come from the sidecar.
_SIDECAR_FIELDS: tuple[str, ...] = (
    "species_slug",
    "species_common_name",
    "style_slug",
    "style_name",
    "provider",
    "model",
    "image_size",
    "prompt",
    "variation",
    "seed_requested",
    "seed_used",
    "supports_seed",
    "generated_at",
    "raw_path",
)

# Fields that are mutated by later pipeline stages and must be preserved when
# rebuilding the manifest.
_PRESERVED_FIELDS: tuple[str, ...] = (
    "normalized_path",
    "selected_as_master",
)


def load_manifest() -> list[dict[str, Any]]:
    """Load the manifest from ``MANIFEST_PATH``.

    Returns an empty list if the file does not yet exist. Raises
    ``ValueError`` if the file exists but does not contain a JSON array.
    """
    if not MANIFEST_PATH.exists():
        return []
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(
            f"Manifest at {MANIFEST_PATH} must be a JSON array, got {type(data).__name__}"
        )
    return data


def save_manifest(records: list[dict[str, Any]]) -> None:
    """Write ``records`` to ``MANIFEST_PATH`` with indent=2 UTF-8."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def find_record(
    records: list[dict[str, Any]],
    species_slug: str,
    style_slug: str,
    variation: int,
) -> dict[str, Any] | None:
    """Return the first record matching the composite key, or ``None``."""
    for rec in records:
        if (
            rec.get("species_slug") == species_slug
            and rec.get("style_slug") == style_slug
            and int(rec.get("variation", -1)) == int(variation)
        ):
            return rec
    return None


def _record_key(rec: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(rec.get("species_slug", "")),
        str(rec.get("style_slug", "")),
        int(rec.get("variation", 0)),
    )


def _load_sidecar(sidecar_path: Path) -> dict[str, Any] | None:
    """Load a single ``.json`` sidecar into a manifest record.

    Returns ``None`` on any parse error (logged as a warning).
    """
    try:
        with sidecar_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read sidecar %s: %s", sidecar_path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Sidecar %s did not contain a JSON object", sidecar_path)
        return None

    record: dict[str, Any] = {field: data.get(field) for field in _SIDECAR_FIELDS}

    # Default fields that may not be set yet; merging preserves any existing
    # values from the previous manifest.
    record["normalized_path"] = data.get("normalized_path")
    record["selected_as_master"] = bool(data.get("selected_as_master", False))

    return record


def scan_sidecars(raw_dir: Path) -> list[dict[str, Any]]:
    """Walk ``raw_dir`` and return one record per ``.json`` sidecar found."""
    if not raw_dir.exists():
        logger.info("Raw dir %s does not exist; no sidecars to scan", raw_dir)
        return []

    records: list[dict[str, Any]] = []
    for sidecar_path in sorted(raw_dir.rglob("*.json")):
        record = _load_sidecar(sidecar_path)
        if record is None:
            continue
        # Require the minimal key fields to be present.
        if not record.get("species_slug") or not record.get("style_slug"):
            logger.warning(
                "Sidecar %s is missing species_slug/style_slug; skipping",
                sidecar_path,
            )
            continue
        if record.get("variation") is None:
            logger.warning(
                "Sidecar %s is missing variation; skipping", sidecar_path
            )
            continue
        records.append(record)

    records.sort(key=_record_key)
    return records


def rebuild_manifest(*, verbose: bool = False) -> list[dict[str, Any]]:
    """Rebuild the manifest from disk, preserving mutable fields.

    Walks ``RAW_DIR`` for sidecars, loads the existing manifest (if any), and
    merges preserved fields by composite key. Writes the result to
    ``MANIFEST_PATH`` and returns the new records list.
    """
    fresh = scan_sidecars(RAW_DIR)
    existing = load_manifest()
    existing_by_key: dict[tuple[str, str, int], dict[str, Any]] = {
        _record_key(rec): rec for rec in existing
    }

    merged: list[dict[str, Any]] = []
    preserved_count = 0
    for rec in fresh:
        key = _record_key(rec)
        prior = existing_by_key.get(key)
        if prior is not None:
            for field in _PRESERVED_FIELDS:
                if field in prior and prior[field] not in (None, False):
                    rec[field] = prior[field]
                    preserved_count += 1
        merged.append(rec)

    save_manifest(merged)

    species = {r["species_slug"] for r in merged}
    styles = {r["style_slug"] for r in merged}
    print(
        f"Found {len(merged)} records across {len(species)} species and "
        f"{len(styles)} styles"
    )
    if verbose:
        print(f"Preserved {preserved_count} mutable field value(s) from prior manifest")
        for rec in merged:
            print(
                f"  {rec['style_slug']}/{rec['species_slug']} v{rec['variation']} "
                f"-> {rec.get('raw_path')}"
            )

    return merged


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild metadata/manifest.json from raw sidecar JSONs."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every record found during scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    rebuild_manifest(verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
