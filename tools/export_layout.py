"""Run the FieldGuideBandsEngine for the 12-fish cast and export positions
as JSON for the customer-facing designer to load.

Overrides common_carp's relative_scale_index to a more reasonable value
(1.4) since 2.2 makes it visually compete with the pike hero.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, "/Users/grant/claude-workspace/Wildlife/wildprint")

from poster_layout import (
    FileSystemMasterImageLoader,
    PosterSpec,
    SpeciesRef,
    select_layout_engine,
)
from config.settings import MASTER_DIR, SPECIES_JSON

CANVAS_W, CANVAS_H = 5400, 7200

CAST = [
    "northern_pike", "largemouth_bass", "smallmouth_bass",
    "bowfin", "yellow_perch", "rock_bass",
    "common_carp", "white_sucker", "bullhead_catfish",
    "black_crappie", "bluegill", "channel_catfish",
]

# Override these idx values to balance the visual hierarchy
IDX_OVERRIDES = {
    "common_carp": 1.4,  # was 2.2 — was too close to pike (2.5)
}

catalog = {s["slug"]: s for s in json.load(open(SPECIES_JSON))}
refs = []
for slug in CAST:
    rec = catalog[slug]
    idx = IDX_OVERRIDES.get(slug, rec.get("relative_scale_index", 1.0))
    refs.append(SpeciesRef(
        slug=rec["slug"],
        common_name=rec["common_name"],
        scientific_name=rec.get("scientific_name", ""),
        category=rec.get("category", "fish"),
        relative_scale_index=float(idx),
        habitat_tags=list(rec.get("habitat_tags", [])),
        water_column=rec.get("water_column", "mid"),
    ))

spec = PosterSpec(
    title="Your Lake Here",
    subtitle=None,
    style_slug="scientific",
    species_slugs=CAST,
    layout_style="field_guide_packed",
    canvas_width=CANVAS_W,
    canvas_height=CANVAS_H,
    background_color="#FAF6EA",
)

loader = FileSystemMasterImageLoader(masters_dir=MASTER_DIR)
# Override engine settings: bigger hero target so all fish render larger.
# The engine's binary search still guarantees no bbox overlap, so fish grow
# until something binds (label clearance or body region height), then back off.
from poster_layout.engines import FieldGuideBandsEngine
engine = FieldGuideBandsEngine(
    scale_compression=0.65,
    hero_target_w_fraction=0.85,  # was 0.65 — pushes fish 30% larger
)
layout = engine.layout(spec, refs, loader)

out_data = {
    "canvas": {"w": CANVAS_W, "h": CANVAS_H},
    "title": {"main": "Your Lake Here", "preheader": "FISH OF"},
    "placements": [
        {
            "slug": p.species_ref.slug,
            "common_name": p.species_ref.common_name,
            "scientific_name": p.species_ref.scientific_name,
            "x": p.x, "y": p.y,
            "w": p.draw_width, "h": p.draw_height,
            "image_url": f"/output/master/scientific/{p.species_ref.slug}.png",
        }
        for p in layout.placements
    ],
}
out_path = Path("/Users/grant/claude-workspace/Wildlife/wildprint/output/customizer/layout_12fish.json")
out_path.write_text(json.dumps(out_data, indent=2))
print(f"wrote {len(layout.placements)} placements to {out_path}")
