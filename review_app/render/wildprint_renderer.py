"""Adapter wiring :class:`RenderSpec` to ``poster_layout.EditorialMultiRenderer``.

This is the production ``MasterRenderer`` used by :func:`render_tier`. It
exists so :mod:`review_app.render` can stay free of any direct knowledge of
``poster_layout`` while still producing real customer posters at tier 1/2/3.

Contract — same as :data:`MasterRenderer`::

    (spec: RenderSpec, width: int, height: int) -> PIL.Image.Image

The adapter:

1. Loads the species catalog (config.settings.SPECIES_JSON) to hydrate
   :class:`SpeciesRef` objects from the slugs in ``spec.species``.
2. Filters to species that actually have masters on disk for the chosen
   ``art_style`` (matches the style_slug used by /create).
3. Builds a :class:`PosterSpec` at the requested canvas size.
4. Picks the layout engine via :func:`select_layout_engine`.
5. Calls :class:`EditorialMultiRenderer.render` to a temporary PNG, then
   loads the result back into a :class:`PIL.Image.Image` and returns it.

The temp PNG round-trip is the simplest way to honor the existing
``EditorialMultiRenderer`` signature (which writes to a Path) without
modifying ``poster_layout``. ``poster_layout/renderer.py`` is the crown
jewel — we wrap it, we never touch it.

Per-spec customization knobs (logo, frame style, preheader, etc.) are
honored via ``spec.layout_config``. Unknown keys are ignored. Unset keys
fall back to ``EditorialMultiRenderer`` defaults so a minimal spec still
produces a correct poster.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from review_app.render.spec import RenderSpec

logger = logging.getLogger(__name__)


def _load_species_catalog() -> dict[str, dict[str, Any]]:
    """Return ``{slug: species_dict}`` from the project catalog.

    Imported lazily so the render module can be imported in environments
    without the full poster_layout dependency tree (test fixtures).
    """
    import json

    from config.settings import SPECIES_JSON

    try:
        with open(SPECIES_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if isinstance(slug, str):
            out[slug] = entry
    return out


def _build_species_refs(slugs: list[str]) -> list[Any]:
    """Hydrate :class:`SpeciesRef` instances from slugs via the catalog."""
    from poster_layout import SpeciesRef

    catalog = _load_species_catalog()
    refs: list[Any] = []
    for slug in slugs:
        rec = catalog.get(slug)
        if rec is None:
            # Unknown slug — fail loud rather than silently dropping it.
            raise ValueError(
                f"wildprint_renderer: unknown species slug {slug!r}; not in "
                "the catalog. Verify spec.species against config/species.json."
            )
        refs.append(
            SpeciesRef(
                slug=rec["slug"],
                common_name=rec.get("common_name", rec["slug"]),
                scientific_name=rec.get("scientific_name", ""),
                category=rec.get("category", ""),
                relative_scale_index=float(
                    rec.get("relative_scale_index", 1.0) or 1.0
                ),
                habitat_tags=list(rec.get("habitat_tags", [])),
                water_column=rec.get("water_column", "mid"),
            )
        )
    return refs


def render_master_image(
    spec: RenderSpec,
    width: int,
    height: int,
) -> Image.Image:
    """Produce the master image for ``spec`` at ``width`` x ``height``.

    See module docstring for the contract. Returns an RGB or RGBA
    :class:`PIL.Image.Image` at the requested canvas size.

    Raises:
        RuntimeError: if no species in ``spec.species`` has a master on
            disk for ``spec.art_style``. Surfaces clearly so route handlers
            can return a 4xx instead of producing a blank poster.
    """
    # Late imports so test fixtures that monkey-patch this module don't
    # pull in the full poster_layout dependency tree.
    from config.settings import MASTER_DIR
    from poster_layout import (
        EditorialMultiRenderer,
        FileSystemMasterImageLoader,
        PosterSpec,
        select_layout_engine,
    )

    style_slug = spec.art_style
    species_refs = _build_species_refs(list(spec.species))

    loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)
    present_refs = [
        ref for ref in species_refs if loader.exists(ref.slug, style_slug)
    ]
    if not present_refs:
        raise RuntimeError(
            "wildprint_renderer: no master images found for "
            f"style={style_slug!r} species={list(spec.species)!r}. "
            f"Check {MASTER_DIR}/{style_slug}/."
        )

    cfg = dict(spec.layout_config or {})
    title = str(cfg.get("title") or spec.lake)
    subtitle = cfg.get("subtitle")
    background_color = str(cfg.get("background_color") or "#FFFFFF")

    poster_spec = PosterSpec(
        title=title,
        subtitle=subtitle,
        style_slug=style_slug,
        species_slugs=[ref.slug for ref in present_refs],
        layout_style=str(cfg.get("layout_style") or "hero"),
        canvas_width=int(width),
        canvas_height=int(height),
        background_color=background_color,
        show_labels=bool(cfg.get("show_labels", True)),
    )

    engine = select_layout_engine(present_refs, poster_spec)
    layout = engine.layout(poster_spec, present_refs, loader)

    if not layout.placements:
        raise RuntimeError(
            "wildprint_renderer: layout engine produced zero placements for "
            f"style={style_slug!r} species={[r.slug for r in present_refs]!r}."
        )

    renderer = EditorialMultiRenderer()

    # Honor a small whitelist of layout_config knobs that match what
    # /api/generate-poster sets. Unknown keys are ignored; we deliberately
    # do not silently expose every renderer attribute.
    if "show_scientific_names" in cfg:
        renderer._show_scientific_names = bool(cfg["show_scientific_names"])
    if "preheader_text" in cfg and isinstance(cfg["preheader_text"], str):
        renderer._preheader_text = cfg["preheader_text"].upper()
    if "frame_style" in cfg and cfg["frame_style"] in (
        "walnut",
        "oak",
        "black",
        "white",
        "pine",
    ):
        renderer._frame_style = cfg["frame_style"]

    # Render to a tempfile, then load back into PIL. EditorialMultiRenderer
    # writes a PNG; we hand the caller the loaded Image so the tier-encode
    # path can resize/encode without an intermediate disk hop.
    with tempfile.TemporaryDirectory(prefix="wildprint-render-") as tmp:
        out_path = Path(tmp) / "master.png"
        renderer.render(layout, out_path)
        with Image.open(out_path) as src:
            # ``load()`` forces the lazy decoder so the file can be
            # garbage-collected when the tempdir disappears.
            src.load()
            master = src.copy()

    logger.info(
        "wildprint_renderer rendered master spec_hash=%s size=%dx%d species=%d",
        spec.canonical_hash()[:12],
        master.size[0],
        master.size[1],
        len(present_refs),
    )
    return master


__all__ = ["render_master_image"]
