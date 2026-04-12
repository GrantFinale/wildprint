#!/usr/bin/env python3
"""Build the wildprint US wildlife catalog.

Generates species.json, habitat_profiles.json, and regions.json with
~150 US freshwater species.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------
REGIONS = {
    "northeast": ["ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA"],
    "southeast": ["VA", "WV", "NC", "SC", "GA", "FL", "AL", "MS", "LA", "AR", "TN", "KY"],
    "midwest": ["OH", "MI", "IN", "IL", "WI", "MN", "IA", "MO", "ND", "SD", "NE", "KS"],
    "southwest": ["TX", "OK", "NM", "AZ"],
    "mountain_west": ["MT", "WY", "CO", "UT", "ID", "NV"],
    "pacific_northwest": ["WA", "OR"],
    "california": ["CA"],
    "alaska": ["AK"],
}

# ---------------------------------------------------------------------------
# Preserve existing species
# ---------------------------------------------------------------------------
existing = json.loads((DATA_DIR / "species" / "species.json").read_text())
existing_slugs = {sp["slug"] for sp in existing}
existing_profiles = json.loads((DATA_DIR / "species" / "habitat_profiles.json").read_text())

# ---------------------------------------------------------------------------
# New species definitions
# ---------------------------------------------------------------------------
new_species: list[dict] = []
new_profiles: dict[str, dict] = {}


def add(sp: dict, profile: dict) -> None:
    sp.setdefault("enabled", True)
    new_species.append(sp)
    new_profiles[sp["slug"]] = profile


# ===== FISH =====

add(
    {
        "common_name": "Spotted Bass",
        "slug": "spotted_bass",
        "scientific_name": "Micropterus punctulatus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "rocky"],
        "key_visual_traits": [
            "moderately deep body, body length is 2.5-3 times body depth",
            "rows of dark spots forming lines below the lateral line",
            "olive-green back with dark mottled blotches along the midline",
            "connected dorsal fin with shallow notch between spiny and soft portions",
            "small rough tooth patch on the tongue",
        ],
        "relative_scale_index": 1.1,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["southeast", "midwest", "southwest"],
        "notes": "Spot rows below lateral line distinguish from largemouth.",
    },
    {
        "water_type": {"lake": 7, "pond": 4, "river": 10, "stream": 7, "reservoir": 9, "marsh": 2},
        "depth": {"shallow": 5, "moderate": 9, "deep": 6},
        "flow": {"still": 4, "slow": 6, "moderate": 10, "fast": 5},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 9, "none": 6},
        "clarity": {"clear": 9, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Striped Bass",
        "slug": "striped_bass",
        "scientific_name": "Morone saxatilis",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "coastal"],
        "key_visual_traits": [
            "elongated streamlined body, body length is 3.5 times body depth",
            "silver sides with 7-8 bold dark horizontal stripes running from gill to tail",
            "two separate dorsal fins",
            "large mouth with projecting lower jaw",
            "olive-green to steel-blue back fading to white belly",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["northeast", "southeast", "midwest", "southwest"],
        "notes": "Landlocked populations in many reservoirs.",
    },
    {
        "water_type": {"lake": 8, "pond": 2, "river": 10, "stream": 3, "reservoir": 9, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 9},
        "flow": {"still": 4, "slow": 6, "moderate": 9, "fast": 7},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 8},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "White Bass",
        "slug": "white_bass",
        "scientific_name": "Morone chrysops",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "reservoirs", "rivers"],
        "key_visual_traits": [
            "deep-bodied and laterally compressed, body length is 2.5 times body depth",
            "silver-white sides with faint dark horizontal stripes",
            "two separate dorsal fins with a gap between them",
            "arched back and humped profile",
            "single tooth patch on the base of the tongue",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["midwest", "southeast", "southwest", "northeast"],
        "notes": "Schooling predator; spring spawning runs up tributaries.",
    },
    {
        "water_type": {"lake": 9, "pond": 3, "river": 8, "stream": 3, "reservoir": 10, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 5, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 8},
        "clarity": {"clear": 8, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Guadalupe Bass",
        "slug": "guadalupe_bass",
        "scientific_name": "Micropterus treculii",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "rocky"],
        "key_visual_traits": [
            "moderately elongated body, body length is 2.5 times body depth",
            "olive-green back with dark diamond-shaped blotches along the lateral line",
            "rows of dark spots on the lower sides",
            "distinctive dark bars radiating from the snout and eye",
            "connected dorsal fin with shallow notch",
        ],
        "relative_scale_index": 0.7,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["southwest"],
        "notes": "State fish of Texas; endemic to Edwards Plateau streams.",
    },
    {
        "water_type": {"lake": 3, "pond": 2, "river": 9, "stream": 10, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 7, "moderate": 8, "deep": 3},
        "flow": {"still": 2, "slow": 5, "moderate": 10, "fast": 7},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 8, "none": 7},
        "clarity": {"clear": 10, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Redear Sunfish",
        "slug": "redear_sunfish",
        "scientific_name": "Lepomis microlophus",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "ponds", "weedy"],
        "key_visual_traits": [
            "deep, laterally compressed disc-shaped body, body length is 1.8 times body depth",
            "olive-green back with lighter green-yellow sides",
            "distinctive red or orange margin on the black ear flap",
            "no dark blotch on the dorsal fin base (unlike bluegill)",
            "small downturned mouth suited for crushing snails",
        ],
        "relative_scale_index": 0.55,
        "target_aspect_ratio": 1.7,
        "geographic_range": ["southeast", "midwest", "southwest"],
        "notes": "Also called shellcracker; feeds heavily on snails.",
    },
    {
        "water_type": {"lake": 9, "pond": 10, "river": 4, "stream": 3, "reservoir": 7, "marsh": 5},
        "depth": {"shallow": 8, "moderate": 7, "deep": 3},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 4},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Green Sunfish",
        "slug": "green_sunfish",
        "scientific_name": "Lepomis cyanellus",
        "category": "fish",
        "habitat_tags": ["freshwater", "streams", "ponds", "pools"],
        "key_visual_traits": [
            "stocky, thick-bodied sunfish, body length is 2 times body depth",
            "olive to bluish-green body with faint blue-green streaks on face",
            "large mouth extending past the front of the eye",
            "black ear flap with pale whitish margin",
            "yellow-orange margins on anal and caudal fins",
        ],
        "relative_scale_index": 0.45,
        "target_aspect_ratio": 1.8,
        "geographic_range": ["midwest", "southeast", "southwest", "northeast"],
        "notes": "Tolerates poor water quality; large mouth for a sunfish.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 6, "stream": 10, "reservoir": 5, "marsh": 5},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 7, "slow": 8, "moderate": 6, "fast": 3},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 6, "none": 5},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

add(
    {
        "common_name": "Longear Sunfish",
        "slug": "longear_sunfish",
        "scientific_name": "Lepomis megalotis",
        "category": "fish",
        "habitat_tags": ["freshwater", "streams", "rivers", "clear_water"],
        "key_visual_traits": [
            "deep compressed body, body length is 1.8 times body depth",
            "brilliantly colored with orange belly and turquoise wavy lines on face",
            "very long black ear flap bordered by white or blue margin",
            "orange-red spots scattered on sides",
            "short rounded pectoral fins",
        ],
        "relative_scale_index": 0.4,
        "target_aspect_ratio": 1.6,
        "geographic_range": ["midwest", "southeast", "southwest"],
        "notes": "Extremely long opercular flap is diagnostic.",
    },
    {
        "water_type": {"lake": 5, "pond": 6, "river": 8, "stream": 10, "reservoir": 4, "marsh": 3},
        "depth": {"shallow": 9, "moderate": 6, "deep": 2},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 8, "none": 5},
        "clarity": {"clear": 10, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Warmouth",
        "slug": "warmouth",
        "scientific_name": "Lepomis gulosus",
        "category": "fish",
        "habitat_tags": ["freshwater", "swamps", "ponds", "muddy"],
        "key_visual_traits": [
            "stocky, thick-bodied sunfish, body length is 2 times body depth",
            "dark olive-brown body with mottled pattern",
            "red-brown streaks radiating back from the red eye",
            "large mouth extending past the front of the eye",
            "three dark lines radiating behind the eye across the cheek",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.8,
        "geographic_range": ["southeast", "midwest", "southwest"],
        "notes": "Prefers murky, vegetated waters; tolerates low oxygen.",
    },
    {
        "water_type": {"lake": 5, "pond": 8, "river": 4, "stream": 3, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 10, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 10, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 3, "moderate": 5, "murky": 10},
    },
)

add(
    {
        "common_name": "Redbreast Sunfish",
        "slug": "redbreast_sunfish",
        "scientific_name": "Lepomis auritus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "sandy"],
        "key_visual_traits": [
            "moderately deep body, body length is 2 times body depth",
            "olive back with bright orange-red breast and belly",
            "long narrow black ear flap without light margin",
            "blue-green wavy lines on face",
            "small mouth not extending past front of eye",
        ],
        "relative_scale_index": 0.45,
        "target_aspect_ratio": 1.7,
        "geographic_range": ["northeast", "southeast"],
        "notes": "Bright orange breast distinctive in eastern streams.",
    },
    {
        "water_type": {"lake": 5, "pond": 5, "river": 9, "stream": 10, "reservoir": 4, "marsh": 3},
        "depth": {"shallow": 8, "moderate": 7, "deep": 3},
        "flow": {"still": 4, "slow": 7, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 8, "none": 5},
        "clarity": {"clear": 9, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "White Crappie",
        "slug": "white_crappie",
        "scientific_name": "Pomoxis annularis",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "reservoirs", "turbid"],
        "key_visual_traits": [
            "deep, strongly compressed silvery body, body length is 2 times body depth",
            "dark vertical bars on silvery-green sides (unlike black crappie spots)",
            "five or six dorsal spines (fewer than black crappie)",
            "longer more pointed snout than black crappie",
            "large dorsal and anal fins of similar size",
        ],
        "relative_scale_index": 0.6,
        "target_aspect_ratio": 2.0,
        "geographic_range": ["midwest", "southeast", "southwest", "northeast"],
        "notes": "Vertical bars distinguish from irregularly spotted black crappie.",
    },
    {
        "water_type": {"lake": 9, "pond": 6, "river": 6, "stream": 3, "reservoir": 10, "marsh": 4},
        "depth": {"shallow": 5, "moderate": 9, "deep": 6},
        "flow": {"still": 8, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 7, "none": 5},
        "clarity": {"clear": 6, "moderate": 8, "murky": 6},
    },
)

add(
    {
        "common_name": "Blue Catfish",
        "slug": "blue_catfish",
        "scientific_name": "Ictalurus furcatus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "deep_water"],
        "key_visual_traits": [
            "large, elongated heavy body, body length is 3.5 times body depth",
            "blue-gray to slate back fading to white belly without spots",
            "deeply forked tail",
            "straight-edged anal fin with 30-36 rays",
            "four pairs of barbels and smooth scaleless skin",
        ],
        "relative_scale_index": 3.0,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["southeast", "midwest", "southwest"],
        "notes": "Largest North American catfish; straight anal fin margin is key.",
    },
    {
        "water_type": {"lake": 6, "pond": 2, "river": 10, "stream": 3, "reservoir": 9, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 7, "deep": 10},
        "flow": {"still": 3, "slow": 5, "moderate": 9, "fast": 6},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 9},
        "clarity": {"clear": 5, "moderate": 7, "murky": 8},
    },
)

add(
    {
        "common_name": "Flathead Catfish",
        "slug": "flathead_catfish",
        "scientific_name": "Pylodictis olivaris",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "deep_pools"],
        "key_visual_traits": [
            "large, heavy body with broad flat head, body length is 3.5 times body depth",
            "mottled yellow-brown to olive coloration",
            "distinctly flattened head with protruding lower jaw",
            "squared or slightly notched tail (not deeply forked)",
            "small eyes set wide apart on the broad head",
        ],
        "relative_scale_index": 3.0,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["midwest", "southeast", "southwest"],
        "notes": "Flat head and squared tail distinguish from channel and blue cats.",
    },
    {
        "water_type": {"lake": 5, "pond": 2, "river": 10, "stream": 4, "reservoir": 8, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 7, "deep": 10},
        "flow": {"still": 3, "slow": 5, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 9},
        "clarity": {"clear": 5, "moderate": 7, "murky": 8},
    },
)

add(
    {
        "common_name": "Chain Pickerel",
        "slug": "chain_pickerel",
        "scientific_name": "Esox niger",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "ponds", "weedy"],
        "key_visual_traits": [
            "elongated torpedo-shaped body, body length is 4.5 times body depth",
            "olive-green to yellow sides with distinctive dark chain-link pattern",
            "long pointed snout with large mouth and sharp teeth",
            "fully scaled cheek and gill cover",
            "single dorsal fin set far back near the tail",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 4.5,
        "geographic_range": ["northeast", "southeast"],
        "notes": "Chain-link pattern on sides is diagnostic; fully scaled opercle.",
    },
    {
        "water_type": {"lake": 9, "pond": 8, "river": 5, "stream": 4, "reservoir": 6, "marsh": 6},
        "depth": {"shallow": 9, "moderate": 6, "deep": 3},
        "flow": {"still": 9, "slow": 8, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 10, "moderate": 8, "sparse": 4, "none": 2},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Muskellunge",
        "slug": "muskellunge",
        "scientific_name": "Esox masquinongy",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "weedy"],
        "key_visual_traits": [
            "very large elongated torpedo body, body length is 5 times body depth",
            "light silver-green body with dark bars or spots (never light spots on dark)",
            "long flat duckbill snout with large teeth",
            "pointed tail fin lobes",
            "sensory pores on the lower jaw (6-9 per side)",
        ],
        "relative_scale_index": 3.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["northeast", "midwest", "mountain_west"],
        "notes": "Dark markings on light background; opposite of northern pike pattern.",
    },
    {
        "water_type": {"lake": 10, "pond": 3, "river": 7, "stream": 3, "reservoir": 7, "marsh": 4},
        "depth": {"shallow": 7, "moderate": 9, "deep": 6},
        "flow": {"still": 7, "slow": 8, "moderate": 6, "fast": 3},
        "vegetation": {"heavy": 9, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Tiger Musky",
        "slug": "tiger_musky",
        "scientific_name": "Esox masquinongy × lucius",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "reservoirs"],
        "key_visual_traits": [
            "large elongated body, body length is 4.5 times body depth",
            "bold dark vertical bars on a light greenish-silver background",
            "tiger-stripe pattern combining traits of pike and musky",
            "rounded tail fin lobes (unlike pointed musky or forked pike)",
            "long flat snout with large teeth",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 4.5,
        "geographic_range": ["midwest", "northeast", "mountain_west"],
        "notes": "Sterile hybrid (pike × musky); stocked for sport fishing.",
    },
    {
        "water_type": {"lake": 10, "pond": 4, "river": 6, "stream": 2, "reservoir": 8, "marsh": 3},
        "depth": {"shallow": 7, "moderate": 8, "deep": 5},
        "flow": {"still": 7, "slow": 8, "moderate": 5, "fast": 3},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Walleye",
        "slug": "walleye",
        "scientific_name": "Sander vitreus",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "reservoirs"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 3.5 times body depth",
            "olive-gold sides with brassy flecks and dark saddle markings",
            "large glassy opaque eyes with reflective tapetum lucidum",
            "white tip on the lower lobe of the forked tail",
            "two separate dorsal fins, the first with a dark blotch at the base",
        ],
        "relative_scale_index": 1.6,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["midwest", "northeast", "mountain_west", "southeast"],
        "notes": "Reflective eyes adapted for low-light feeding; prized sport fish.",
    },
    {
        "water_type": {"lake": 10, "pond": 3, "river": 8, "stream": 4, "reservoir": 9, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 8, "deep": 9},
        "flow": {"still": 5, "slow": 6, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 8, "none": 7},
        "clarity": {"clear": 7, "moderate": 9, "murky": 5},
    },
)

add(
    {
        "common_name": "Sauger",
        "slug": "sauger",
        "scientific_name": "Sander canadensis",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "turbid"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 3.5 times body depth",
            "brassy-brown sides with dark saddle-shaped blotches",
            "large glassy eyes similar to walleye",
            "distinct dark spots on the first dorsal fin membrane",
            "no white tip on the tail fin (unlike walleye)",
        ],
        "relative_scale_index": 1.1,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["midwest", "southeast", "mountain_west"],
        "notes": "Tolerates more turbid water than walleye; spotted dorsal is key.",
    },
    {
        "water_type": {"lake": 7, "pond": 2, "river": 10, "stream": 4, "reservoir": 8, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 7, "deep": 9},
        "flow": {"still": 4, "slow": 6, "moderate": 9, "fast": 5},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 8},
        "clarity": {"clear": 5, "moderate": 8, "murky": 7},
    },
)

add(
    {
        "common_name": "Saugeye",
        "slug": "saugeye",
        "scientific_name": "Sander vitreus × canadensis",
        "category": "fish",
        "habitat_tags": ["freshwater", "reservoirs", "lakes", "rivers"],
        "key_visual_traits": [
            "elongated body, body length is 3.5 times body depth",
            "intermediate appearance between walleye and sauger",
            "brassy-olive sides with faint saddle markings",
            "large glassy eyes",
            "faint dark spots on dorsal fin with faint white tail tip",
        ],
        "relative_scale_index": 1.3,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Walleye-sauger hybrid; widely stocked in reservoirs.",
    },
    {
        "water_type": {"lake": 8, "pond": 3, "river": 7, "stream": 3, "reservoir": 10, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 5, "slow": 6, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 6, "moderate": 8, "murky": 6},
    },
)

# --- Trout ---

add(
    {
        "common_name": "Rainbow Trout",
        "slug": "rainbow_trout",
        "scientific_name": "Oncorhynchus mykiss",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "cold_water"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "distinctive pink-red lateral stripe from gill to tail",
            "dark spots scattered across body and fins",
            "silvery sides with olive-green back",
            "slightly forked tail fin",
        ],
        "relative_scale_index": 1.3,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["pacific_northwest", "mountain_west", "northeast", "midwest", "california"],
        "notes": "Widely stocked across the US.",
    },
    {
        "water_type": {"lake": 6, "pond": 2, "river": 10, "stream": 10, "reservoir": 6, "marsh": 1},
        "depth": {"shallow": 4, "moderate": 8, "deep": 7},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 8, "none": 6},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

add(
    {
        "common_name": "Brown Trout",
        "slug": "brown_trout",
        "scientific_name": "Salmo trutta",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "cold_water"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "golden-brown sides with large dark spots ringed in lighter halos",
            "red-orange spots scattered along the lateral line",
            "no spots on the square-cut tail fin",
            "adipose fin often with orange tint",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["northeast", "midwest", "mountain_west", "pacific_northwest", "southeast"],
        "notes": "European introduction; more tolerant of warm water than other trout.",
    },
    {
        "water_type": {"lake": 6, "pond": 2, "river": 10, "stream": 10, "reservoir": 6, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 7},
        "flow": {"still": 2, "slow": 5, "moderate": 9, "fast": 9},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 8, "none": 6},
        "clarity": {"clear": 9, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "Brook Trout",
        "slug": "brook_trout",
        "scientific_name": "Salvelinus fontinalis",
        "category": "fish",
        "habitat_tags": ["freshwater", "streams", "cold_springs", "cold_water"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "dark olive-green back with distinctive pale worm-like vermiculations",
            "red spots with blue halos along the flanks",
            "bright orange belly with white leading edges on lower fins",
            "square or slightly forked tail fin",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["northeast", "southeast", "midwest", "mountain_west"],
        "notes": "Actually a char (Salvelinus); needs coldest and cleanest water.",
    },
    {
        "water_type": {"lake": 4, "pond": 3, "river": 7, "stream": 10, "reservoir": 3, "marsh": 1},
        "depth": {"shallow": 6, "moderate": 7, "deep": 4},
        "flow": {"still": 2, "slow": 4, "moderate": 8, "fast": 10},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 7},
        "clarity": {"clear": 10, "moderate": 5, "murky": 1},
    },
)

add(
    {
        "common_name": "Lake Trout",
        "slug": "lake_trout",
        "scientific_name": "Salvelinus namaycush",
        "category": "fish",
        "habitat_tags": ["freshwater", "deep_lakes", "cold_water"],
        "key_visual_traits": [
            "large elongated body, body length is 3.5 times body depth",
            "dark gray-green body covered in pale cream-colored spots",
            "deeply forked tail fin",
            "light-colored leading edges on lower fins",
            "large head with slightly forked caudal fin",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["northeast", "midwest", "mountain_west", "alaska"],
        "notes": "Deep cold lake specialist; largest native char in North America.",
    },
    {
        "water_type": {"lake": 10, "pond": 1, "river": 2, "stream": 2, "reservoir": 7, "marsh": 1},
        "depth": {"shallow": 2, "moderate": 5, "deep": 10},
        "flow": {"still": 8, "slow": 5, "moderate": 3, "fast": 2},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 5, "none": 9},
        "clarity": {"clear": 10, "moderate": 5, "murky": 1},
    },
)

add(
    {
        "common_name": "Cutthroat Trout",
        "slug": "cutthroat_trout",
        "scientific_name": "Oncorhynchus clarkii",
        "category": "fish",
        "habitat_tags": ["freshwater", "streams", "rivers", "cold_water"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "distinctive red-orange slash marks under the lower jaw",
            "olive-green to yellowish body with dark spots concentrated toward tail",
            "spots on the tail fin (unlike brown trout)",
            "small teeth on the back of the tongue",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["mountain_west", "pacific_northwest", "california", "alaska"],
        "notes": "Red throat slash marks are diagnostic; many subspecies.",
    },
    {
        "water_type": {"lake": 5, "pond": 2, "river": 9, "stream": 10, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 5},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 7},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

add(
    {
        "common_name": "Bull Trout",
        "slug": "bull_trout",
        "scientific_name": "Salvelinus confluentus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "cold_water"],
        "key_visual_traits": [
            "elongated body with large flat head, body length is 3.5 times body depth",
            "olive-green to gray body with small pale yellow and orange spots",
            "large broad head with slightly flattened profile",
            "no black spots on dorsal fin",
            "white leading edges on lower fins",
        ],
        "relative_scale_index": 1.8,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["pacific_northwest", "mountain_west"],
        "notes": "Threatened species; requires coldest and cleanest habitat.",
    },
    {
        "water_type": {"lake": 5, "pond": 1, "river": 9, "stream": 10, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 4, "moderate": 8, "deep": 7},
        "flow": {"still": 2, "slow": 3, "moderate": 8, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 7, "none": 8},
        "clarity": {"clear": 10, "moderate": 5, "murky": 1},
    },
)

add(
    {
        "common_name": "Apache Trout",
        "slug": "apache_trout",
        "scientific_name": "Oncorhynchus apache",
        "category": "fish",
        "habitat_tags": ["freshwater", "streams", "cold_water", "mountain"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "golden-yellow body with dark spots evenly spaced",
            "distinctive dark eye mask through and behind the eye",
            "spots extend below the lateral line to the belly",
            "slightly forked tail with spots",
        ],
        "relative_scale_index": 0.7,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["southwest"],
        "notes": "Endangered; endemic to White Mountains of Arizona.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 5, "stream": 10, "reservoir": 2, "marsh": 1},
        "depth": {"shallow": 7, "moderate": 7, "deep": 3},
        "flow": {"still": 1, "slow": 3, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 7, "none": 8},
        "clarity": {"clear": 10, "moderate": 5, "murky": 1},
    },
)

# --- Salmon ---

add(
    {
        "common_name": "Chinook Salmon",
        "slug": "chinook_salmon",
        "scientific_name": "Oncorhynchus tshawytscha",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous", "cold_water"],
        "key_visual_traits": [
            "large robust body, body length is 3 times body depth",
            "blue-green to dark back with silver sides",
            "black spots on back, dorsal fin, and both lobes of the tail",
            "black gum line at base of teeth in lower jaw",
            "large adipose fin and deeply forked tail",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["pacific_northwest", "california", "alaska"],
        "notes": "Largest Pacific salmon; also called king salmon.",
    },
    {
        "water_type": {"lake": 3, "pond": 1, "river": 10, "stream": 7, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 8},
        "clarity": {"clear": 9, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Coho Salmon",
        "slug": "coho_salmon",
        "scientific_name": "Oncorhynchus kisutch",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous", "cold_water"],
        "key_visual_traits": [
            "moderately elongated body, body length is 3 times body depth",
            "metallic blue back with bright silver sides",
            "small black spots on back and upper lobe of tail only",
            "white gum line (distinguishes from chinook)",
            "red flanks and green head in spawning males",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["pacific_northwest", "california", "alaska"],
        "notes": "Also called silver salmon; spots on upper tail lobe only.",
    },
    {
        "water_type": {"lake": 3, "pond": 1, "river": 10, "stream": 8, "reservoir": 3, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 7},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 7},
        "clarity": {"clear": 9, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Sockeye Salmon",
        "slug": "sockeye_salmon",
        "scientific_name": "Oncorhynchus nerka",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "anadromous"],
        "key_visual_traits": [
            "streamlined body, body length is 3 times body depth",
            "metallic blue back with silver sides in ocean phase",
            "bright red body with green head in spawning phase",
            "no distinct spots on back or tail",
            "large prominent eye and small mouth with tiny teeth",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["alaska", "pacific_northwest"],
        "notes": "Bright red spawning coloration; lake-rearing juveniles.",
    },
    {
        "water_type": {"lake": 8, "pond": 1, "river": 9, "stream": 6, "reservoir": 3, "marsh": 1},
        "depth": {"shallow": 4, "moderate": 7, "deep": 9},
        "flow": {"still": 4, "slow": 5, "moderate": 8, "fast": 9},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 8},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

add(
    {
        "common_name": "Atlantic Salmon",
        "slug": "atlantic_salmon",
        "scientific_name": "Salmo salar",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous", "cold_water"],
        "key_visual_traits": [
            "streamlined elongated body, body length is 3.5 times body depth",
            "silver sides with scattered X-shaped dark spots above lateral line",
            "small head relative to body with slightly forked tail",
            "adipose fin present",
            "dark back with steel-blue to green tint",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["northeast"],
        "notes": "Native to NE rivers; can spawn multiple times unlike Pacific salmon.",
    },
    {
        "water_type": {"lake": 3, "pond": 1, "river": 10, "stream": 8, "reservoir": 3, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 7},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 7, "none": 7},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

add(
    {
        "common_name": "Pink Salmon",
        "slug": "pink_salmon",
        "scientific_name": "Oncorhynchus gorbuscha",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous"],
        "key_visual_traits": [
            "smallest Pacific salmon, body length is 2.5 times body depth",
            "steel blue back with silver sides and large oval black spots on back and tail",
            "males develop prominent humped back during spawning",
            "very small scales relative to body size",
            "forked tail with spots on both lobes",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 2.8,
        "geographic_range": ["alaska", "pacific_northwest"],
        "notes": "Also called humpback salmon; strict two-year life cycle.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 10, "stream": 7, "reservoir": 2, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 7, "deep": 6},
        "flow": {"still": 2, "slow": 4, "moderate": 8, "fast": 10},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 7},
        "clarity": {"clear": 9, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Kokanee",
        "slug": "kokanee",
        "scientific_name": "Oncorhynchus nerka (landlocked)",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "cold_water"],
        "key_visual_traits": [
            "streamlined compact body, body length is 3 times body depth",
            "bright silver sides with blue-green back",
            "no distinct spots on back or tail",
            "bright red body with green head during spawning",
            "smaller than anadromous sockeye",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["pacific_northwest", "mountain_west", "california"],
        "notes": "Landlocked form of sockeye salmon; important forage and sport fish.",
    },
    {
        "water_type": {"lake": 10, "pond": 2, "river": 3, "stream": 3, "reservoir": 8, "marsh": 1},
        "depth": {"shallow": 3, "moderate": 6, "deep": 10},
        "flow": {"still": 7, "slow": 5, "moderate": 3, "fast": 2},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 8},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

# --- Sturgeon ---

add(
    {
        "common_name": "Lake Sturgeon",
        "slug": "lake_sturgeon",
        "scientific_name": "Acipenser fulvescens",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "deep_water"],
        "key_visual_traits": [
            "very large elongated shark-like body, body length is 5 times body depth",
            "rows of bony scutes along the sides and back",
            "long pointed snout with four barbels on the underside",
            "heterocercal tail with upper lobe longer than lower",
            "olive-brown to gray body with lighter belly",
        ],
        "relative_scale_index": 4.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["midwest", "northeast"],
        "notes": "Ancient fish; can live over 100 years. Threatened.",
    },
    {
        "water_type": {"lake": 10, "pond": 1, "river": 8, "stream": 2, "reservoir": 6, "marsh": 1},
        "depth": {"shallow": 3, "moderate": 6, "deep": 10},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 9},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "White Sturgeon",
        "slug": "white_sturgeon",
        "scientific_name": "Acipenser transmontanus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "estuaries", "deep_water"],
        "key_visual_traits": [
            "massive elongated body, body length is 5 times body depth",
            "gray to pale olive body with white belly",
            "rows of bony scutes less prominent than in other sturgeon",
            "long broad snout with four barbels",
            "heterocercal tail fin",
        ],
        "relative_scale_index": 5.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["pacific_northwest", "california"],
        "notes": "Largest North American freshwater fish; can exceed 6 meters.",
    },
    {
        "water_type": {"lake": 3, "pond": 1, "river": 10, "stream": 2, "reservoir": 5, "marsh": 1},
        "depth": {"shallow": 2, "moderate": 5, "deep": 10},
        "flow": {"still": 3, "slow": 5, "moderate": 8, "fast": 7},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 5, "none": 9},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Atlantic Sturgeon",
        "slug": "atlantic_sturgeon",
        "scientific_name": "Acipenser oxyrinchus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "estuaries", "anadromous"],
        "key_visual_traits": [
            "very large elongated body, body length is 5 times body depth",
            "blue-black to olive-green back with lighter sides",
            "five rows of prominent bony scutes along the body",
            "long pointed snout with four barbels",
            "heterocercal tail with elongated upper lobe",
        ],
        "relative_scale_index": 4.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["northeast", "southeast"],
        "notes": "Endangered; historically from Maine to Florida.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 10, "stream": 2, "reservoir": 3, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 6, "deep": 10},
        "flow": {"still": 3, "slow": 5, "moderate": 8, "fast": 7},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 5, "none": 9},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Pallid Sturgeon",
        "slug": "pallid_sturgeon",
        "scientific_name": "Scaphirhynchus albus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "turbid", "deep_channels"],
        "key_visual_traits": [
            "elongated flattened body, body length is 5 times body depth",
            "very pale white-gray body (paler than shovelnose sturgeon)",
            "long flat shovel-shaped snout",
            "armored with bony plates along body",
            "long filamentous upper tail lobe",
        ],
        "relative_scale_index": 3.5,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Critically endangered; Missouri and Mississippi river systems.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 10, "stream": 2, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 2, "moderate": 6, "deep": 10},
        "flow": {"still": 2, "slow": 4, "moderate": 8, "fast": 8},
        "vegetation": {"heavy": 1, "moderate": 2, "sparse": 5, "none": 10},
        "clarity": {"clear": 3, "moderate": 6, "murky": 9},
    },
)

add(
    {
        "common_name": "Shovelnose Sturgeon",
        "slug": "shovelnose_sturgeon",
        "scientific_name": "Scaphirhynchus platorynchus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "sandy_bottom"],
        "key_visual_traits": [
            "small elongated sturgeon, body length is 5 times body depth",
            "light brown body with bony scutes along sides",
            "flat shovel-shaped snout wider than pallid sturgeon",
            "barbels with fringed bases",
            "long thin caudal peduncle armored with bony plates",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Most common large-river sturgeon in the Mississippi basin.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 10, "stream": 3, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 3, "moderate": 7, "deep": 9},
        "flow": {"still": 2, "slow": 4, "moderate": 9, "fast": 8},
        "vegetation": {"heavy": 1, "moderate": 2, "sparse": 5, "none": 10},
        "clarity": {"clear": 4, "moderate": 7, "murky": 8},
    },
)

# --- Paddlefish ---

add(
    {
        "common_name": "Paddlefish",
        "slug": "paddlefish",
        "scientific_name": "Polyodon spathula",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "reservoirs", "open_water"],
        "key_visual_traits": [
            "large body with enormous paddle-shaped rostrum, body length is 4 times body depth",
            "smooth scaleless gray-blue body",
            "elongated paddle-like snout one-third of total length",
            "large gaping mouth for filter feeding",
            "heterocercal shark-like tail with elongated upper lobe",
        ],
        "relative_scale_index": 3.5,
        "target_aspect_ratio": 4.5,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Filter feeder with electroreceptive rostrum; living fossil.",
    },
    {
        "water_type": {"lake": 5, "pond": 1, "river": 10, "stream": 2, "reservoir": 8, "marsh": 2},
        "depth": {"shallow": 3, "moderate": 6, "deep": 10},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 8},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

# --- Gar ---

add(
    {
        "common_name": "Longnose Gar",
        "slug": "longnose_gar",
        "scientific_name": "Lepisosteus osseus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "lakes", "backwaters"],
        "key_visual_traits": [
            "extremely elongated cylindrical body, body length is 6 times body depth",
            "very long narrow snout more than twice the length of the head",
            "olive-brown back with lighter sides covered in dark spots",
            "hard diamond-shaped ganoid scales",
            "rounded tail fin with body extending into the upper lobe",
        ],
        "relative_scale_index": 3.0,
        "target_aspect_ratio": 6.0,
        "geographic_range": ["midwest", "southeast", "northeast", "southwest"],
        "notes": "Longest and most widespread gar species.",
    },
    {
        "water_type": {"lake": 7, "pond": 4, "river": 9, "stream": 5, "reservoir": 7, "marsh": 4},
        "depth": {"shallow": 7, "moderate": 7, "deep": 4},
        "flow": {"still": 6, "slow": 8, "moderate": 7, "fast": 4},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 6, "none": 4},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Shortnose Gar",
        "slug": "shortnose_gar",
        "scientific_name": "Lepisosteus platostomus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "backwaters", "turbid"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 5 times body depth",
            "short broad snout (shortest of all gars)",
            "olive to brown body with dark spots on fins but not body",
            "hard diamond-shaped ganoid scales",
            "rounded tail fin",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Shortest snout of all gars; spots mainly on fins.",
    },
    {
        "water_type": {"lake": 5, "pond": 4, "river": 10, "stream": 4, "reservoir": 6, "marsh": 5},
        "depth": {"shallow": 7, "moderate": 7, "deep": 4},
        "flow": {"still": 6, "slow": 8, "moderate": 6, "fast": 3},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 6, "none": 5},
        "clarity": {"clear": 4, "moderate": 7, "murky": 8},
    },
)

add(
    {
        "common_name": "Alligator Gar",
        "slug": "alligator_gar",
        "scientific_name": "Atractosteus spatula",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "bayous", "backwaters"],
        "key_visual_traits": [
            "massive elongated body, body length is 5 times body depth",
            "broad alligator-like snout with two rows of teeth in the upper jaw",
            "olive-brown to dark green body with lighter belly",
            "hard diamond-shaped ganoid scales",
            "rounded tail fin; largest gar species",
        ],
        "relative_scale_index": 5.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["southeast", "southwest"],
        "notes": "Largest gar; dual rows of upper jaw teeth are diagnostic.",
    },
    {
        "water_type": {"lake": 5, "pond": 3, "river": 9, "stream": 2, "reservoir": 6, "marsh": 8},
        "depth": {"shallow": 7, "moderate": 7, "deep": 5},
        "flow": {"still": 8, "slow": 9, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 4},
        "clarity": {"clear": 4, "moderate": 6, "murky": 8},
    },
)

add(
    {
        "common_name": "Spotted Gar",
        "slug": "spotted_gar",
        "scientific_name": "Lepisosteus oculatus",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "weedy"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 5 times body depth",
            "dark spots covering head, body, and all fins",
            "olive-brown body with lighter belly",
            "moderately long snout between longnose and shortnose gar",
            "hard diamond-shaped ganoid scales",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["southeast", "midwest", "southwest"],
        "notes": "Spots on head and body distinguish from other gars.",
    },
    {
        "water_type": {"lake": 8, "pond": 5, "river": 7, "stream": 3, "reservoir": 6, "marsh": 6},
        "depth": {"shallow": 8, "moderate": 6, "deep": 3},
        "flow": {"still": 7, "slow": 8, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 4, "none": 3},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Florida Gar",
        "slug": "florida_gar",
        "scientific_name": "Lepisosteus platyrhincus",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "canals", "swamps"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 5 times body depth",
            "olive-brown body with dark irregular spots",
            "broad snout similar to spotted gar",
            "lacks bony plate on the underside of the snout (unlike spotted gar)",
            "hard diamond-shaped ganoid scales",
        ],
        "relative_scale_index": 1.8,
        "target_aspect_ratio": 5.0,
        "geographic_range": ["southeast"],
        "notes": "Endemic to Florida and southern Georgia.",
    },
    {
        "water_type": {"lake": 8, "pond": 6, "river": 5, "stream": 3, "reservoir": 5, "marsh": 8},
        "depth": {"shallow": 8, "moderate": 6, "deep": 3},
        "flow": {"still": 9, "slow": 8, "moderate": 4, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

# --- Shad ---

add(
    {
        "common_name": "American Shad",
        "slug": "american_shad",
        "scientific_name": "Alosa sapidissima",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous"],
        "key_visual_traits": [
            "deep compressed body, body length is 2.5 times body depth",
            "silvery sides with blue-green to dark blue back",
            "one or more dark spots behind the gill cover in a row",
            "large easily shed scales",
            "deeply forked tail fin",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["northeast", "southeast", "pacific_northwest"],
        "notes": "Largest North American herring; anadromous spawning runs.",
    },
    {
        "water_type": {"lake": 3, "pond": 1, "river": 10, "stream": 5, "reservoir": 5, "marsh": 1},
        "depth": {"shallow": 4, "moderate": 8, "deep": 7},
        "flow": {"still": 3, "slow": 5, "moderate": 9, "fast": 7},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 8},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Gizzard Shad",
        "slug": "gizzard_shad",
        "scientific_name": "Dorosoma cepedianum",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "reservoirs", "rivers"],
        "key_visual_traits": [
            "deep compressed body, body length is 2.5 times body depth",
            "silvery sides with blue-gray back",
            "dark spot behind the gill cover",
            "last ray of dorsal fin extended into a long filament",
            "subterminal mouth with blunt snout",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["midwest", "southeast", "northeast", "southwest"],
        "notes": "Important forage fish; elongated dorsal ray is diagnostic.",
    },
    {
        "water_type": {"lake": 8, "pond": 5, "river": 7, "stream": 3, "reservoir": 9, "marsh": 4},
        "depth": {"shallow": 5, "moderate": 8, "deep": 6},
        "flow": {"still": 7, "slow": 7, "moderate": 5, "fast": 3},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

add(
    {
        "common_name": "Hickory Shad",
        "slug": "hickory_shad",
        "scientific_name": "Alosa mediocris",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "anadromous"],
        "key_visual_traits": [
            "elongated compressed body, body length is 3 times body depth",
            "silvery sides with grayish-green back",
            "row of dark spots behind gill cover",
            "strongly projecting lower jaw",
            "deeply forked tail with dark edges",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["northeast", "southeast"],
        "notes": "Projecting lower jaw distinguishes from American shad.",
    },
    {
        "water_type": {"lake": 2, "pond": 1, "river": 10, "stream": 4, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 6},
        "flow": {"still": 3, "slow": 5, "moderate": 8, "fast": 7},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 7},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

# --- Drum ---

add(
    {
        "common_name": "Freshwater Drum",
        "slug": "freshwater_drum",
        "scientific_name": "Aplodinotus grunniens",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "reservoirs"],
        "key_visual_traits": [
            "deep heavy body with humped back, body length is 2.5 times body depth",
            "silvery-gray sides with darker back",
            "long dorsal fin with spiny and soft portions connected",
            "rounded snout with subterminal mouth",
            "large otoliths (ear bones) visible as lucky stones",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["midwest", "southeast", "northeast", "southwest"],
        "notes": "Only North American freshwater member of the drum family.",
    },
    {
        "water_type": {"lake": 9, "pond": 4, "river": 8, "stream": 3, "reservoir": 8, "marsh": 3},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

# --- Sucker/Buffalo ---

add(
    {
        "common_name": "Longnose Sucker",
        "slug": "longnose_sucker",
        "scientific_name": "Catostomus catostomus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "streams", "cold_water"],
        "key_visual_traits": [
            "elongated cylindrical body, body length is 4 times body depth",
            "dark olive to brown back with lighter sides",
            "long protruding snout extending well past the upper lip",
            "downward-facing sucker mouth with papillose lips",
            "fine scales and single dorsal fin",
        ],
        "relative_scale_index": 1.0,
        "target_aspect_ratio": 4.0,
        "geographic_range": ["northeast", "midwest", "mountain_west", "pacific_northwest", "alaska"],
        "notes": "Cold-water sucker; longer snout than white sucker.",
    },
    {
        "water_type": {"lake": 7, "pond": 2, "river": 9, "stream": 8, "reservoir": 5, "marsh": 2},
        "depth": {"shallow": 5, "moderate": 8, "deep": 6},
        "flow": {"still": 3, "slow": 5, "moderate": 8, "fast": 8},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 8},
        "clarity": {"clear": 9, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "Smallmouth Buffalo",
        "slug": "smallmouth_buffalo",
        "scientific_name": "Ictiobus bubalus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "lakes", "reservoirs"],
        "key_visual_traits": [
            "deep, heavy-bodied fish, body length is 2 times body depth",
            "dark bronze to olive-brown body",
            "small subterminal mouth angled downward",
            "high-arched back and deep compressed body",
            "long dorsal fin with more than 25 rays",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 2.0,
        "geographic_range": ["midwest", "southeast", "southwest"],
        "notes": "Small downward mouth distinguishes from bigmouth buffalo.",
    },
    {
        "water_type": {"lake": 7, "pond": 3, "river": 9, "stream": 4, "reservoir": 8, "marsh": 4},
        "depth": {"shallow": 4, "moderate": 8, "deep": 7},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

add(
    {
        "common_name": "Bigmouth Buffalo",
        "slug": "bigmouth_buffalo",
        "scientific_name": "Ictiobus cyprinellus",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "rivers", "backwaters"],
        "key_visual_traits": [
            "large deep-bodied fish, body length is 2 times body depth",
            "dark olive to bronze body with large scales",
            "large terminal oblique mouth facing forward",
            "high-arched back",
            "long dorsal fin and rounded tail",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 2.0,
        "geographic_range": ["midwest", "southeast"],
        "notes": "Forward-facing mouth for filter feeding; can live over 100 years.",
    },
    {
        "water_type": {"lake": 8, "pond": 4, "river": 8, "stream": 3, "reservoir": 7, "marsh": 6},
        "depth": {"shallow": 5, "moderate": 8, "deep": 6},
        "flow": {"still": 7, "slow": 8, "moderate": 6, "fast": 3},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 6, "none": 5},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

# --- Whitefish ---

add(
    {
        "common_name": "Lake Whitefish",
        "slug": "lake_whitefish",
        "scientific_name": "Coregonus clupeaformis",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "cold_water", "deep_water"],
        "key_visual_traits": [
            "streamlined body with small head, body length is 3.5 times body depth",
            "silvery sides with olive-green to brown back",
            "small adipose fin and forked tail",
            "small mouth with overhanging snout",
            "large easily shed scales",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["midwest", "northeast", "alaska"],
        "notes": "Important commercial species in the Great Lakes.",
    },
    {
        "water_type": {"lake": 10, "pond": 1, "river": 3, "stream": 2, "reservoir": 5, "marsh": 1},
        "depth": {"shallow": 3, "moderate": 6, "deep": 10},
        "flow": {"still": 7, "slow": 5, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 6, "none": 8},
        "clarity": {"clear": 9, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Cisco",
        "slug": "cisco",
        "scientific_name": "Coregonus artedi",
        "category": "fish",
        "habitat_tags": ["freshwater", "lakes", "cold_water", "deep_water"],
        "key_visual_traits": [
            "slender streamlined body, body length is 4 times body depth",
            "silvery iridescent sides with blue-green to dark back",
            "slightly projecting lower jaw",
            "small adipose fin",
            "large thin easily shed scales",
        ],
        "relative_scale_index": 1.0,
        "target_aspect_ratio": 4.0,
        "geographic_range": ["midwest", "northeast"],
        "notes": "Also called lake herring; important forage fish in deep lakes.",
    },
    {
        "water_type": {"lake": 10, "pond": 1, "river": 2, "stream": 2, "reservoir": 4, "marsh": 1},
        "depth": {"shallow": 3, "moderate": 5, "deep": 10},
        "flow": {"still": 8, "slow": 5, "moderate": 3, "fast": 2},
        "vegetation": {"heavy": 1, "moderate": 3, "sparse": 6, "none": 9},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

# --- Mooneye/Goldeye ---

add(
    {
        "common_name": "Mooneye",
        "slug": "mooneye",
        "scientific_name": "Hiodon tergisus",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "lakes", "clear_water"],
        "key_visual_traits": [
            "deep compressed body, body length is 2.5 times body depth",
            "bright silvery sides with large eye and golden iris",
            "sharply keeled belly",
            "dorsal fin origin ahead of anal fin origin",
            "deeply forked tail fin",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["midwest", "northeast"],
        "notes": "Large eye adapted for clear water; keeled belly.",
    },
    {
        "water_type": {"lake": 7, "pond": 2, "river": 9, "stream": 5, "reservoir": 5, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 5},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 9, "moderate": 6, "murky": 3},
    },
)

add(
    {
        "common_name": "Goldeye",
        "slug": "goldeye",
        "scientific_name": "Hiodon alosoides",
        "category": "fish",
        "habitat_tags": ["freshwater", "rivers", "lakes", "turbid"],
        "key_visual_traits": [
            "deep compressed body, body length is 2.5 times body depth",
            "bright silvery sides with large golden-yellow eye",
            "keeled belly",
            "dorsal fin origin behind anal fin origin",
            "deeply forked tail fin",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 2.5,
        "geographic_range": ["midwest", "mountain_west"],
        "notes": "Tolerates turbid water; dorsal fin set farther back than mooneye.",
    },
    {
        "water_type": {"lake": 7, "pond": 2, "river": 9, "stream": 4, "reservoir": 5, "marsh": 3},
        "depth": {"shallow": 4, "moderate": 8, "deep": 5},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)


# ===== BIRDS =====

add(
    {
        "common_name": "Green Heron",
        "slug": "green_heron",
        "scientific_name": "Butorides virescens",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "ponds", "streams"],
        "key_visual_traits": [
            "small stocky heron, body height is 1.5-2 times body width, short legs",
            "dark greenish-black cap with slight crest",
            "deep chestnut neck and breast",
            "greenish-blue back and wings",
            "short yellow-orange legs and thick dagger-like bill",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 0.6,
        "geographic_range": ["northeast", "southeast", "midwest", "southwest"],
        "notes": "One of the few tool-using birds; drops bait to attract fish.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 6, "stream": 7, "reservoir": 4, "marsh": 8},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 8, "slow": 8, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 9, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Little Blue Heron",
        "slug": "little_blue_heron",
        "scientific_name": "Egretta caerulea",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "ponds"],
        "key_visual_traits": [
            "medium heron standing on long legs, body height is 2.5 times body width",
            "entirely dark slate-blue plumage in adults",
            "purplish-maroon head and neck",
            "blue-gray bill with black tip",
            "greenish-yellow legs",
        ],
        "relative_scale_index": 1.4,
        "target_aspect_ratio": 0.45,
        "geographic_range": ["southeast", "northeast", "southwest"],
        "notes": "Juveniles are all white; adults entirely dark blue.",
    },
    {
        "water_type": {"lake": 5, "pond": 8, "river": 5, "stream": 4, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Great Egret",
        "slug": "great_egret",
        "scientific_name": "Ardea alba",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "shorelines"],
        "key_visual_traits": [
            "tall elegant standing pose, body height is 3-4 times body width",
            "entirely white plumage",
            "long yellow-orange dagger-like bill",
            "long black legs and feet",
            "S-curved neck and long plume-like aigrettes on back during breeding",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 0.35,
        "geographic_range": ["southeast", "northeast", "midwest", "southwest", "california", "pacific_northwest"],
        "notes": "Symbol of the Audubon Society; hunted nearly to extinction for plumes.",
    },
    {
        "water_type": {"lake": 7, "pond": 8, "river": 6, "stream": 4, "reservoir": 5, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 5, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 7, "moderate": 8, "sparse": 6, "none": 4},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Snowy Egret",
        "slug": "snowy_egret",
        "scientific_name": "Egretta thula",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "shorelines"],
        "key_visual_traits": [
            "medium elegant standing pose, body height is 2.5 times body width",
            "entirely white plumage",
            "slender black bill and black legs with bright yellow feet",
            "yellow lores between bill and eye",
            "filamentous plumes on head, neck, and back during breeding",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 0.4,
        "geographic_range": ["southeast", "northeast", "southwest", "california"],
        "notes": "Golden slippers (yellow feet) distinguish from great egret.",
    },
    {
        "water_type": {"lake": 6, "pond": 8, "river": 5, "stream": 4, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 7, "moderate": 8, "sparse": 6, "none": 4},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Black-crowned Night Heron",
        "slug": "black_crowned_night_heron",
        "scientific_name": "Nycticorax nycticorax",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "ponds"],
        "key_visual_traits": [
            "stocky compact heron, body height is 1.5 times body width",
            "black cap and back contrasting with pale gray wings",
            "white underparts and face",
            "short thick neck and stocky build",
            "large red eyes and thick black bill",
        ],
        "relative_scale_index": 1.4,
        "target_aspect_ratio": 0.7,
        "geographic_range": ["nationwide"],
        "notes": "Nocturnal feeder; most widespread heron in the world.",
    },
    {
        "water_type": {"lake": 7, "pond": 9, "river": 6, "stream": 4, "reservoir": 5, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 6, "murky": 7},
    },
)

add(
    {
        "common_name": "Yellow-crowned Night Heron",
        "slug": "yellow_crowned_night_heron",
        "scientific_name": "Nyctanassa violacea",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "swamps"],
        "key_visual_traits": [
            "stocky heron, body height is 1.5-2 times body width",
            "blue-gray body with bold black and white facial pattern",
            "creamy-yellow crown stripe",
            "longer legs than black-crowned night heron",
            "thick black bill and red eye",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 0.6,
        "geographic_range": ["southeast", "southwest", "northeast"],
        "notes": "Crab specialist; longer legs allow wading in deeper water.",
    },
    {
        "water_type": {"lake": 4, "pond": 7, "river": 5, "stream": 4, "reservoir": 3, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 6, "murky": 7},
    },
)

add(
    {
        "common_name": "Least Bittern",
        "slug": "least_bittern",
        "scientific_name": "Ixobrychus exilis",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "reed_beds", "cattails"],
        "key_visual_traits": [
            "very small heron, body height is 1.5 times body width",
            "dark back and cap with buffy-orange neck and sides",
            "large buffy wing patches visible in flight",
            "dagger-shaped yellow bill",
            "greenish-yellow legs and ability to straddle reeds",
        ],
        "relative_scale_index": 0.6,
        "target_aspect_ratio": 0.6,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Smallest North American heron; clings to reeds with long toes.",
    },
    {
        "water_type": {"lake": 3, "pond": 6, "river": 2, "stream": 2, "reservoir": 3, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 10, "slow": 6, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 10, "moderate": 6, "sparse": 2, "none": 1},
        "clarity": {"clear": 5, "moderate": 6, "murky": 7},
    },
)

add(
    {
        "common_name": "Tricolored Heron",
        "slug": "tricolored_heron",
        "scientific_name": "Egretta tricolor",
        "category": "bird",
        "habitat_tags": ["freshwater", "wetlands", "marshes", "coastal"],
        "key_visual_traits": [
            "slender medium heron, body height is 2.5 times body width",
            "dark blue-gray upperparts with white belly and flanks",
            "rufous neck stripe in adults",
            "long thin bill with yellow base",
            "long yellow to greenish legs",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 0.45,
        "geographic_range": ["southeast", "southwest"],
        "notes": "White belly contrasting with dark body is diagnostic.",
    },
    {
        "water_type": {"lake": 4, "pond": 7, "river": 4, "stream": 3, "reservoir": 3, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 5, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 4},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

# --- Ducks ---

add(
    {
        "common_name": "Blue-winged Teal",
        "slug": "blue_winged_teal",
        "scientific_name": "Spatula discors",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "prairie_potholes"],
        "key_visual_traits": [
            "small dabbling duck, body length is 1.3-1.5 times body height when swimming",
            "male with bold white crescent on blue-gray face",
            "chalky-blue forewing patch (speculum border)",
            "warm brown body with dark spots",
            "small bill and compact body",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "One of the latest spring and earliest fall migrants.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 3, "stream": 2, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 10, "slow": 7, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 8, "sparse": 4, "none": 2},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Green-winged Teal",
        "slug": "green_winged_teal",
        "scientific_name": "Anas crecca",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "mudflats"],
        "key_visual_traits": [
            "very small compact duck, body length is 1.3 times body height",
            "male with chestnut head and broad green eye patch",
            "vertical white stripe on the side of the breast",
            "green speculum bordered by buff",
            "gray body with fine vermiculations",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "Smallest North American dabbling duck.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 4, "stream": 3, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Canvasback",
        "slug": "canvasback",
        "scientific_name": "Aythya valisineria",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "marshes", "bays"],
        "key_visual_traits": [
            "large diving duck with distinctive sloped forehead, body length is 1.5 times body height",
            "male with rich chestnut-red head and long sloping black bill",
            "white back and sides (canvas-like)",
            "black breast and tail",
            "distinctive wedge-shaped head profile",
        ],
        "relative_scale_index": 1.3,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["midwest", "pacific_northwest", "california", "mountain_west"],
        "notes": "Fastest flying duck; head profile slopes evenly from crown to bill tip.",
    },
    {
        "water_type": {"lake": 9, "pond": 5, "river": 3, "stream": 1, "reservoir": 7, "marsh": 8},
        "depth": {"shallow": 5, "moderate": 8, "deep": 7},
        "flow": {"still": 9, "slow": 6, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 4},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Redhead",
        "slug": "redhead",
        "scientific_name": "Aythya americana",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "marshes", "ponds"],
        "key_visual_traits": [
            "medium diving duck, body length is 1.4 times body height",
            "male with rounded bright reddish-copper head",
            "gray back and sides",
            "black breast and tail",
            "blue-gray bill with black tip and white ring",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["midwest", "mountain_west", "southwest"],
        "notes": "Rounded head shape distinguishes from sloped canvasback profile.",
    },
    {
        "water_type": {"lake": 8, "pond": 6, "river": 3, "stream": 1, "reservoir": 7, "marsh": 9},
        "depth": {"shallow": 6, "moderate": 8, "deep": 5},
        "flow": {"still": 9, "slow": 6, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Ring-necked Duck",
        "slug": "ring_necked_duck",
        "scientific_name": "Aythya collaris",
        "category": "bird",
        "habitat_tags": ["freshwater", "ponds", "lakes", "marshes"],
        "key_visual_traits": [
            "medium diving duck with peaked head, body length is 1.4 times body height",
            "male with dark purple-black head with peaked crown",
            "gray flanks with white spur curving up in front of wing",
            "bold white ring around the bill tip",
            "black back and breast with golden eye",
        ],
        "relative_scale_index": 1.1,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "Named for hard-to-see neck ring; bill ring is more visible field mark.",
    },
    {
        "water_type": {"lake": 7, "pond": 9, "river": 3, "stream": 2, "reservoir": 6, "marsh": 8},
        "depth": {"shallow": 6, "moderate": 8, "deep": 5},
        "flow": {"still": 9, "slow": 6, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Common Goldeneye",
        "slug": "common_goldeneye",
        "scientific_name": "Bucephala clangula",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "rivers", "boreal"],
        "key_visual_traits": [
            "medium compact diving duck, body length is 1.4 times body height",
            "male with glossy dark green head (appears black) and round white face spot",
            "bright golden-yellow eye",
            "white sides and breast with black back",
            "large puffy triangular head shape",
        ],
        "relative_scale_index": 1.1,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["northeast", "midwest", "mountain_west", "pacific_northwest", "alaska"],
        "notes": "Wings produce distinctive whistling sound in flight.",
    },
    {
        "water_type": {"lake": 9, "pond": 4, "river": 7, "stream": 3, "reservoir": 6, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 6, "slow": 6, "moderate": 6, "fast": 4},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 9, "moderate": 6, "murky": 3},
    },
)

add(
    {
        "common_name": "Bufflehead",
        "slug": "bufflehead",
        "scientific_name": "Bucephala albeola",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "ponds", "boreal"],
        "key_visual_traits": [
            "very small compact diving duck, body length is 1.3 times body height",
            "male with large puffy dark purple-green head with bold white patch",
            "white body with black back",
            "tiny bill and small rounded head",
            "rapid wing beats in flight",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["nationwide"],
        "notes": "Smallest diving duck in North America; nests in woodpecker holes.",
    },
    {
        "water_type": {"lake": 8, "pond": 7, "river": 4, "stream": 2, "reservoir": 6, "marsh": 3},
        "depth": {"shallow": 5, "moderate": 8, "deep": 6},
        "flow": {"still": 8, "slow": 6, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 8, "moderate": 6, "murky": 3},
    },
)

add(
    {
        "common_name": "Common Merganser",
        "slug": "common_merganser",
        "scientific_name": "Mergus merganser",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "rivers", "cold_water"],
        "key_visual_traits": [
            "large elongated diving duck, body length is 1.6 times body height",
            "male with dark green head (no crest) and bright red serrated bill",
            "white body with black back",
            "long streamlined profile for underwater pursuit",
            "red-orange legs and feet",
        ],
        "relative_scale_index": 1.6,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["northeast", "midwest", "mountain_west", "pacific_northwest", "alaska"],
        "notes": "Fish-eating duck with serrated bill for gripping prey.",
    },
    {
        "water_type": {"lake": 9, "pond": 3, "river": 9, "stream": 6, "reservoir": 7, "marsh": 2},
        "depth": {"shallow": 4, "moderate": 8, "deep": 8},
        "flow": {"still": 5, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 2, "moderate": 4, "sparse": 7, "none": 8},
        "clarity": {"clear": 10, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Hooded Merganser",
        "slug": "hooded_merganser",
        "scientific_name": "Lophodytes cucullatus",
        "category": "bird",
        "habitat_tags": ["freshwater", "ponds", "swamps", "wooded_streams"],
        "key_visual_traits": [
            "small compact diving duck, body length is 1.4 times body height",
            "male with large fan-shaped black and white crest",
            "golden-yellow eye",
            "dark back with rusty-brown flanks",
            "thin dark serrated bill",
        ],
        "relative_scale_index": 1.0,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["northeast", "southeast", "midwest", "pacific_northwest"],
        "notes": "Spectacular fan-shaped crest can be raised or flattened.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 5, "stream": 6, "reservoir": 4, "marsh": 7},
        "depth": {"shallow": 7, "moderate": 7, "deep": 4},
        "flow": {"still": 7, "slow": 8, "moderate": 5, "fast": 3},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Ruddy Duck",
        "slug": "ruddy_duck",
        "scientific_name": "Oxyura jamaicensis",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "lakes"],
        "key_visual_traits": [
            "small compact stiff-tailed duck, body length is 1.3 times body height",
            "breeding male with bright blue bill and chestnut body",
            "black cap and white cheek patch",
            "long stiff tail often held upright",
            "compact dumpy body shape",
        ],
        "relative_scale_index": 0.9,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["nationwide"],
        "notes": "Stiff tail held erect during display; bright blue breeding bill.",
    },
    {
        "water_type": {"lake": 7, "pond": 8, "river": 3, "stream": 1, "reservoir": 6, "marsh": 9},
        "depth": {"shallow": 6, "moderate": 8, "deep": 5},
        "flow": {"still": 9, "slow": 6, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Northern Pintail",
        "slug": "northern_pintail",
        "scientific_name": "Anas acuta",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "lakes", "prairie"],
        "key_visual_traits": [
            "elegant slender duck, body length is 1.5 times body height",
            "male with chocolate-brown head and long pointed tail streamers",
            "white breast extending up the neck in a thin stripe",
            "gray body with cream and black rear",
            "long graceful neck and slim profile",
        ],
        "relative_scale_index": 1.4,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Long pointed tail feathers and elegant profile are diagnostic.",
    },
    {
        "water_type": {"lake": 7, "pond": 7, "river": 4, "stream": 2, "reservoir": 5, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 7, "none": 5},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Gadwall",
        "slug": "gadwall",
        "scientific_name": "Mareca strepera",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "lakes"],
        "key_visual_traits": [
            "medium dabbling duck, body length is 1.4 times body height",
            "male with intricate gray-brown vermiculated plumage",
            "black rear end contrasting with gray body",
            "white speculum visible in flight and at rest",
            "gray bill and orange legs",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "Subtle plumage; white speculum patch is best field mark.",
    },
    {
        "water_type": {"lake": 7, "pond": 8, "river": 4, "stream": 2, "reservoir": 6, "marsh": 9},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "American Wigeon",
        "slug": "american_wigeon",
        "scientific_name": "Mareca americana",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "lakes"],
        "key_visual_traits": [
            "medium dabbling duck, body length is 1.4 times body height",
            "male with white forehead cap and green eye patch",
            "warm brown body with pinkish-brown breast",
            "blue-gray bill with black tip",
            "white wing patch visible in flight",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "White forehead blaze on male is distinctive.",
    },
    {
        "water_type": {"lake": 7, "pond": 8, "river": 4, "stream": 2, "reservoir": 5, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Northern Shoveler",
        "slug": "northern_shoveler",
        "scientific_name": "Spatula clypeata",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "ponds", "mudflats"],
        "key_visual_traits": [
            "medium dabbling duck with oversized spatulate bill, body length is 1.4 times body height",
            "male with dark green head, white breast, and chestnut flanks",
            "enormous spatula-shaped black bill",
            "pale blue forewing patch",
            "orange legs",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["nationwide"],
        "notes": "Massive shovel-shaped bill used for filter feeding.",
    },
    {
        "water_type": {"lake": 5, "pond": 8, "river": 3, "stream": 1, "reservoir": 5, "marsh": 10},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 10, "slow": 7, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 6, "none": 4},
        "clarity": {"clear": 4, "moderate": 6, "murky": 8},
    },
)

# --- Geese ---

add(
    {
        "common_name": "Canada Goose",
        "slug": "canada_goose",
        "scientific_name": "Branta canadensis",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "ponds", "rivers", "parks"],
        "key_visual_traits": [
            "large heavy waterfowl, body length is 1.5 times body height",
            "black head and neck with distinctive white chinstrap",
            "brown body with pale breast",
            "long black neck and large webbed feet",
            "V-formation flight silhouette",
        ],
        "relative_scale_index": 2.3,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Most recognized North American goose; year-round in many areas.",
    },
    {
        "water_type": {"lake": 9, "pond": 8, "river": 6, "stream": 3, "reservoir": 7, "marsh": 6},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 8, "slow": 7, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 7, "none": 6},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Snow Goose",
        "slug": "snow_goose",
        "scientific_name": "Anser caerulescens",
        "category": "bird",
        "habitat_tags": ["freshwater", "marshes", "lakes", "agricultural"],
        "key_visual_traits": [
            "large waterfowl, body length is 1.5 times body height",
            "entirely white plumage with black wingtips (white morph)",
            "pink bill with black grin patch along cutting edge",
            "pink legs and feet",
            "blue-gray morph (blue goose) also occurs",
        ],
        "relative_scale_index": 1.8,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Massive migratory flocks; two color morphs (white and blue).",
    },
    {
        "water_type": {"lake": 7, "pond": 6, "river": 4, "stream": 2, "reservoir": 5, "marsh": 9},
        "depth": {"shallow": 9, "moderate": 4, "deep": 1},
        "flow": {"still": 8, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 7, "none": 5},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

# --- Other waterbirds ---

add(
    {
        "common_name": "Double-crested Cormorant",
        "slug": "double_crested_cormorant",
        "scientific_name": "Nannopterum auritum",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "rivers", "coastal"],
        "key_visual_traits": [
            "large dark waterbird, body length is 1.5 times body height when swimming",
            "entirely glossy black plumage with orange throat pouch",
            "hooked bill tip",
            "often seen perched with wings spread to dry",
            "tufted crests on head during breeding",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Lacks waterproof plumage; characteristic wing-drying posture.",
    },
    {
        "water_type": {"lake": 9, "pond": 5, "river": 8, "stream": 3, "reservoir": 8, "marsh": 4},
        "depth": {"shallow": 3, "moderate": 7, "deep": 9},
        "flow": {"still": 7, "slow": 6, "moderate": 6, "fast": 4},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "American White Pelican",
        "slug": "american_white_pelican",
        "scientific_name": "Pelecanus erythrorhynchos",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "marshes", "rivers"],
        "key_visual_traits": [
            "very large white waterbird, body length is 1.5 times body height",
            "entirely white plumage with black flight feathers",
            "enormous orange-yellow bill with expandable pouch",
            "horn-like plate on upper bill during breeding",
            "short orange legs and huge wingspan",
        ],
        "relative_scale_index": 3.5,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["midwest", "mountain_west", "southwest", "california"],
        "notes": "Cooperative surface feeder; does not dive like brown pelican.",
    },
    {
        "water_type": {"lake": 10, "pond": 4, "river": 6, "stream": 2, "reservoir": 7, "marsh": 6},
        "depth": {"shallow": 8, "moderate": 7, "deep": 4},
        "flow": {"still": 8, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Bald Eagle",
        "slug": "bald_eagle",
        "scientific_name": "Haliaeetus leucocephalus",
        "category": "bird",
        "habitat_tags": ["freshwater", "lakes", "rivers", "coastal"],
        "key_visual_traits": [
            "large raptor perched pose, body height is 1.2 times body width, broad chest",
            "adult with distinctive white head and tail",
            "dark brown-black body plumage",
            "large hooked yellow bill and yellow feet with sharp talons",
            "massive wingspan of 2+ meters",
        ],
        "relative_scale_index": 2.2,
        "target_aspect_ratio": 0.9,
        "geographic_range": ["nationwide"],
        "notes": "National bird of the US; recovered from near extinction.",
    },
    {
        "water_type": {"lake": 9, "pond": 4, "river": 9, "stream": 5, "reservoir": 8, "marsh": 5},
        "depth": {"shallow": 6, "moderate": 7, "deep": 7},
        "flow": {"still": 6, "slow": 7, "moderate": 7, "fast": 5},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 7},
        "clarity": {"clear": 9, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Pied-billed Grebe",
        "slug": "pied_billed_grebe",
        "scientific_name": "Podilymbus podiceps",
        "category": "bird",
        "habitat_tags": ["freshwater", "ponds", "marshes", "lakes"],
        "key_visual_traits": [
            "small compact waterbird, body length is 1.3 times body height when swimming",
            "brown plumage with black chin patch during breeding",
            "thick chicken-like bill with black ring (pied bill)",
            "fluffy white rear end",
            "lobed toes rather than webbed feet",
        ],
        "relative_scale_index": 0.7,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["nationwide"],
        "notes": "Can sink gradually below the surface like a submarine.",
    },
    {
        "water_type": {"lake": 7, "pond": 10, "river": 4, "stream": 3, "reservoir": 6, "marsh": 9},
        "depth": {"shallow": 7, "moderate": 7, "deep": 4},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)


# ===== TURTLES =====

add(
    {
        "common_name": "Alligator Snapping Turtle",
        "slug": "alligator_snapping_turtle",
        "scientific_name": "Macrochelys temminckii",
        "category": "turtle",
        "habitat_tags": ["freshwater", "rivers", "bayous", "deep_water"],
        "key_visual_traits": [
            "massive armored shell with three prominent keeled ridges, shell width is 1.3 times shell height",
            "very large head with powerful hooked beak-like jaws",
            "rough dark brown to black carapace covered in algae",
            "worm-like pink tongue lure used to attract prey",
            "spiked shell ridges and heavily armored appearance",
        ],
        "relative_scale_index": 2.0,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["southeast", "southwest"],
        "notes": "Largest freshwater turtle in North America; tongue lure is unique.",
    },
    {
        "water_type": {"lake": 5, "pond": 4, "river": 10, "stream": 4, "reservoir": 6, "marsh": 7},
        "depth": {"shallow": 4, "moderate": 7, "deep": 9},
        "flow": {"still": 6, "slow": 8, "moderate": 6, "fast": 3},
        "vegetation": {"heavy": 6, "moderate": 6, "sparse": 6, "none": 5},
        "clarity": {"clear": 4, "moderate": 6, "murky": 9},
    },
)

add(
    {
        "common_name": "Ornate Box Turtle",
        "slug": "ornate_box_turtle",
        "scientific_name": "Terrapene ornata",
        "category": "turtle",
        "habitat_tags": ["terrestrial", "grasslands", "prairies"],
        "key_visual_traits": [
            "small box turtle with highly domed shell, shell nearly as tall as wide",
            "dark brown to black carapace with bright yellow radiating lines on each scute",
            "hinged plastron allowing complete shell closure",
            "yellow-spotted head and limbs",
            "four toes on the hind feet",
        ],
        "relative_scale_index": 0.35,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["midwest", "southwest"],
        "notes": "Prairie specialist; more yellow lines than eastern box turtle.",
    },
    {
        "water_type": {"lake": 1, "pond": 2, "river": 1, "stream": 2, "reservoir": 1, "marsh": 3},
        "depth": {"shallow": 2, "moderate": 1, "deep": 0},
        "flow": {"still": 2, "slow": 2, "moderate": 1, "fast": 0},
        "vegetation": {"heavy": 3, "moderate": 4, "sparse": 3, "none": 2},
        "clarity": {"clear": 2, "moderate": 2, "murky": 2},
    },
)

add(
    {
        "common_name": "Common Map Turtle",
        "slug": "common_map_turtle",
        "scientific_name": "Graptemys geographica",
        "category": "turtle",
        "habitat_tags": ["freshwater", "rivers", "lakes", "basking"],
        "key_visual_traits": [
            "medium turtle with flattened shell, shell width is 1.4 times shell height",
            "olive to brown carapace with thin yellow-green contour lines resembling a map",
            "prominent keel running along the midline of the carapace",
            "yellow spot behind the eye and yellow-green lines on head and neck",
            "serrated rear edge of carapace",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["midwest", "northeast"],
        "notes": "Map-like lines on shell and skin are diagnostic.",
    },
    {
        "water_type": {"lake": 8, "pond": 4, "river": 10, "stream": 5, "reservoir": 6, "marsh": 3},
        "depth": {"shallow": 5, "moderate": 8, "deep": 5},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 8, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "False Map Turtle",
        "slug": "false_map_turtle",
        "scientific_name": "Graptemys pseudogeographica",
        "category": "turtle",
        "habitat_tags": ["freshwater", "rivers", "lakes", "backwaters"],
        "key_visual_traits": [
            "medium turtle with domed shell, shell width is 1.3 times shell height",
            "olive to brown carapace with thin yellow lines",
            "prominent vertebral keel with knobs",
            "backward-pointing L-shaped or comma-shaped mark behind eye",
            "yellow lines on head converging at the nose",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["midwest", "southeast"],
        "notes": "L-shaped eye mark and keel knobs distinguish from common map.",
    },
    {
        "water_type": {"lake": 7, "pond": 5, "river": 9, "stream": 4, "reservoir": 6, "marsh": 4},
        "depth": {"shallow": 5, "moderate": 8, "deep": 5},
        "flow": {"still": 5, "slow": 7, "moderate": 7, "fast": 4},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 6, "none": 5},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Common Musk Turtle",
        "slug": "common_musk_turtle",
        "scientific_name": "Sternotherus odoratus",
        "category": "turtle",
        "habitat_tags": ["freshwater", "ponds", "streams", "shallow_water"],
        "key_visual_traits": [
            "very small turtle with high-domed shell, shell width is 1.2 times shell height",
            "plain dark olive to brown carapace often covered in algae",
            "two light stripes on each side of the head",
            "very small reduced plastron with one hinge",
            "barbels on chin and neck",
        ],
        "relative_scale_index": 0.3,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Also called stinkpot; releases foul musk when handled.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 5, "stream": 8, "reservoir": 4, "marsh": 5},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 8, "slow": 7, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 4},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

add(
    {
        "common_name": "Eastern Mud Turtle",
        "slug": "eastern_mud_turtle",
        "scientific_name": "Kinosternon subrubrum",
        "category": "turtle",
        "habitat_tags": ["freshwater", "ponds", "ditches", "shallow_marshes"],
        "key_visual_traits": [
            "very small turtle with smooth oval shell, shell width is 1.2 times shell height",
            "plain olive to dark brown smooth carapace without markings",
            "two hinges on the plastron (double-hinged)",
            "small head with yellow mottling",
            "short stubby tail",
        ],
        "relative_scale_index": 0.3,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["southeast", "northeast"],
        "notes": "Double-hinged plastron distinguishes from musk turtles.",
    },
    {
        "water_type": {"lake": 4, "pond": 9, "river": 3, "stream": 5, "reservoir": 3, "marsh": 8},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 3},
        "clarity": {"clear": 4, "moderate": 6, "murky": 8},
    },
)

add(
    {
        "common_name": "River Cooter",
        "slug": "river_cooter",
        "scientific_name": "Pseudemys concinna",
        "category": "turtle",
        "habitat_tags": ["freshwater", "rivers", "lakes", "basking"],
        "key_visual_traits": [
            "large turtle with flattened oval shell, shell width is 1.4 times shell height",
            "dark olive to brown carapace with yellow-green C-shaped markings",
            "dark head with numerous thin yellow stripes",
            "yellowish plastron without bold markings",
            "strongly webbed hind feet for swimming",
        ],
        "relative_scale_index": 0.7,
        "target_aspect_ratio": 1.4,
        "geographic_range": ["southeast", "midwest"],
        "notes": "Herbivorous; C-shaped carapace markings help identify.",
    },
    {
        "water_type": {"lake": 7, "pond": 5, "river": 10, "stream": 5, "reservoir": 6, "marsh": 4},
        "depth": {"shallow": 6, "moderate": 8, "deep": 4},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 4},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 6, "none": 4},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Blanding's Turtle",
        "slug": "blandings_turtle",
        "scientific_name": "Emydoidea blandingii",
        "category": "turtle",
        "habitat_tags": ["freshwater", "marshes", "ponds", "wet_meadows"],
        "key_visual_traits": [
            "medium turtle with high-domed shell, shell height is 0.8 times shell width",
            "dark carapace with scattered yellow spots or streaks",
            "distinctive bright yellow chin and throat",
            "slightly hinged plastron",
            "long neck for a turtle; notched upper jaw",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["midwest", "northeast"],
        "notes": "Threatened; bright yellow throat is diagnostic.",
    },
    {
        "water_type": {"lake": 5, "pond": 8, "river": 3, "stream": 4, "reservoir": 3, "marsh": 10},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Diamondback Terrapin",
        "slug": "diamondback_terrapin",
        "scientific_name": "Malaclemys terrapin",
        "category": "turtle",
        "habitat_tags": ["brackish", "salt_marsh", "estuaries", "coastal"],
        "key_visual_traits": [
            "medium turtle with sculpted shell, shell width is 1.3 times shell height",
            "gray to whitish skin with bold dark spots and speckles",
            "concentric diamond-shaped growth rings on each carapace scute",
            "light-colored shell varying from gray to brown",
            "large webbed hind feet",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["northeast", "southeast"],
        "notes": "Only North American turtle adapted to brackish water.",
    },
    {
        "water_type": {"lake": 1, "pond": 2, "river": 3, "stream": 2, "reservoir": 1, "marsh": 10},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 6, "slow": 7, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 7, "none": 5},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Wood Turtle",
        "slug": "wood_turtle",
        "scientific_name": "Glyptemys insculpta",
        "category": "turtle",
        "habitat_tags": ["freshwater", "streams", "floodplains", "forest"],
        "key_visual_traits": [
            "medium turtle with sculpted shell, shell width is 1.3 times shell height",
            "brown carapace with concentric growth rings giving a sculpted pyramidal look to each scute",
            "orange to reddish-orange coloring on neck and legs",
            "rough textured shell surface",
            "broad flat head with slightly hooked jaw",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["northeast", "midwest"],
        "notes": "Semi-terrestrial; sculpted shell scutes and orange limbs.",
    },
    {
        "water_type": {"lake": 3, "pond": 4, "river": 7, "stream": 10, "reservoir": 2, "marsh": 4},
        "depth": {"shallow": 9, "moderate": 4, "deep": 1},
        "flow": {"still": 4, "slow": 7, "moderate": 7, "fast": 4},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 7, "none": 4},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)


# ===== AMPHIBIANS =====

add(
    {
        "common_name": "American Bullfrog",
        "slug": "american_bullfrog",
        "scientific_name": "Lithobates catesbeianus",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "lakes", "marshes"],
        "key_visual_traits": [
            "large robust frog in sitting pose, body length is 1.2 times body height",
            "olive to green body with darker mottling",
            "large external tympanum (eardrum) larger than the eye",
            "no dorsolateral ridges along the back",
            "fully webbed large hind feet",
        ],
        "relative_scale_index": 0.4,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["nationwide"],
        "notes": "Largest North American frog; tympanum size identifies sex.",
    },
    {
        "water_type": {"lake": 8, "pond": 10, "river": 4, "stream": 3, "reservoir": 6, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 10, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Green Frog",
        "slug": "green_frog",
        "scientific_name": "Lithobates clamitans",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "streams", "marshes"],
        "key_visual_traits": [
            "medium frog in sitting pose, body length is 1.1 times body height",
            "green to brown body with darker blotches",
            "distinct dorsolateral ridges running partway down the back",
            "tympanum equal to or slightly larger than eye",
            "bright green upper lip",
        ],
        "relative_scale_index": 0.25,
        "target_aspect_ratio": 1.1,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Dorsolateral ridges distinguish from bullfrog.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 5, "stream": 7, "reservoir": 4, "marsh": 8},
        "depth": {"shallow": 10, "moderate": 5, "deep": 1},
        "flow": {"still": 8, "slow": 8, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Northern Leopard Frog",
        "slug": "northern_leopard_frog",
        "scientific_name": "Lithobates pipiens",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "meadows", "marshes"],
        "key_visual_traits": [
            "medium slender frog, body length is 1.3 times body height",
            "green to brown body with large oval dark spots with light borders",
            "prominent dorsolateral ridges running full length of body",
            "white belly and white line on upper jaw",
            "long powerful hind legs for leaping",
        ],
        "relative_scale_index": 0.25,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["northeast", "midwest", "mountain_west", "pacific_northwest"],
        "notes": "Leopard-like spots with light borders are diagnostic.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 4, "stream": 5, "reservoir": 4, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Southern Leopard Frog",
        "slug": "southern_leopard_frog",
        "scientific_name": "Lithobates sphenocephalus",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "marshes", "ditches"],
        "key_visual_traits": [
            "medium slender frog, body length is 1.3 times body height",
            "green to brown body with dark rounded spots",
            "pointed snout (more so than northern leopard frog)",
            "light spot in the center of the tympanum",
            "prominent dorsolateral ridges",
        ],
        "relative_scale_index": 0.25,
        "target_aspect_ratio": 1.3,
        "geographic_range": ["southeast", "midwest", "northeast"],
        "notes": "Pointed snout and tympanum spot distinguish from northern.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 4, "stream": 5, "reservoir": 4, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 8, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Wood Frog",
        "slug": "wood_frog",
        "scientific_name": "Lithobates sylvaticus",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "vernal_pools", "forest", "wetlands"],
        "key_visual_traits": [
            "small brown frog, body length is 1.1 times body height",
            "tan to dark brown body",
            "distinctive dark mask through the eye (robber's mask)",
            "light line along the upper jaw",
            "dorsolateral ridges present",
        ],
        "relative_scale_index": 0.18,
        "target_aspect_ratio": 1.1,
        "geographic_range": ["northeast", "midwest", "mountain_west", "alaska"],
        "notes": "Can survive freezing; dark eye mask is diagnostic.",
    },
    {
        "water_type": {"lake": 3, "pond": 7, "river": 2, "stream": 4, "reservoir": 2, "marsh": 6},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 9, "slow": 6, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Spring Peeper",
        "slug": "spring_peeper",
        "scientific_name": "Pseudacris crucifer",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "vernal_pools", "swamps", "forest"],
        "key_visual_traits": [
            "tiny tree frog, body length is 1.0 times body height",
            "tan to brown body with dark X-shaped marking on the back",
            "expanded toe pads for climbing",
            "dark crossbar between the eyes",
            "slightly translucent appearance",
        ],
        "relative_scale_index": 0.08,
        "target_aspect_ratio": 1.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Loud chorus in early spring; X-marking on back is diagnostic.",
    },
    {
        "water_type": {"lake": 3, "pond": 7, "river": 2, "stream": 3, "reservoir": 2, "marsh": 8},
        "depth": {"shallow": 10, "moderate": 2, "deep": 1},
        "flow": {"still": 10, "slow": 6, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 5, "moderate": 6, "murky": 6},
    },
)

add(
    {
        "common_name": "Gray Tree Frog",
        "slug": "gray_tree_frog",
        "scientific_name": "Hyla versicolor",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "forest", "tree_canopy"],
        "key_visual_traits": [
            "small chunky tree frog, body length is 1.0 times body height",
            "rough warty skin varying from gray to green",
            "bright yellow-orange flash colors on inner thighs",
            "white spot beneath each eye",
            "large expanded toe pads for climbing",
        ],
        "relative_scale_index": 0.12,
        "target_aspect_ratio": 1.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Color-changing ability; yellow thigh flash is diagnostic.",
    },
    {
        "water_type": {"lake": 3, "pond": 7, "river": 2, "stream": 4, "reservoir": 2, "marsh": 5},
        "depth": {"shallow": 10, "moderate": 2, "deep": 1},
        "flow": {"still": 9, "slow": 6, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 5, "moderate": 6, "murky": 6},
    },
)

add(
    {
        "common_name": "Pacific Tree Frog",
        "slug": "pacific_tree_frog",
        "scientific_name": "Pseudacris regilla",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "streams", "meadows"],
        "key_visual_traits": [
            "small slender tree frog, body length is 1.1 times body height",
            "green to brown with dark eye stripe from nose to shoulder",
            "expanded toe pads for climbing",
            "smooth skin with varied coloration",
            "vocal sac visible when calling",
        ],
        "relative_scale_index": 0.1,
        "target_aspect_ratio": 1.1,
        "geographic_range": ["pacific_northwest", "california", "mountain_west"],
        "notes": "Most commonly heard frog in Hollywood movies (ribbit).",
    },
    {
        "water_type": {"lake": 4, "pond": 8, "river": 3, "stream": 6, "reservoir": 3, "marsh": 7},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 8, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "American Toad",
        "slug": "american_toad",
        "scientific_name": "Anaxyrus americanus",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "gardens", "forest", "meadows"],
        "key_visual_traits": [
            "stout bumpy toad, body length is 1.0 times body height",
            "brown to reddish-brown warty skin",
            "prominent paired parotoid glands behind the eyes",
            "light dorsal stripe down the middle of the back",
            "one or two large warts in each dark dorsal spot",
        ],
        "relative_scale_index": 0.2,
        "target_aspect_ratio": 1.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Breeds in shallow temporary pools; long trilling call.",
    },
    {
        "water_type": {"lake": 3, "pond": 7, "river": 2, "stream": 4, "reservoir": 2, "marsh": 5},
        "depth": {"shallow": 10, "moderate": 2, "deep": 1},
        "flow": {"still": 9, "slow": 5, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 6, "moderate": 7, "sparse": 6, "none": 4},
        "clarity": {"clear": 5, "moderate": 6, "murky": 6},
    },
)

add(
    {
        "common_name": "Eastern Newt",
        "slug": "eastern_newt",
        "scientific_name": "Notophthalmus viridescens",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "forest", "vernal_pools"],
        "key_visual_traits": [
            "small slender salamander, body length is 3.5 times body depth",
            "adult olive-green body with row of red spots bordered by black",
            "flattened paddle-like tail for swimming",
            "red eft (juvenile) is bright orange-red with red spots",
            "small size with smooth moist skin",
        ],
        "relative_scale_index": 0.25,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Three life stages: larva, red eft (land), adult (aquatic).",
    },
    {
        "water_type": {"lake": 4, "pond": 10, "river": 2, "stream": 4, "reservoir": 3, "marsh": 6},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Spotted Salamander",
        "slug": "spotted_salamander",
        "scientific_name": "Ambystoma maculatum",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "vernal_pools", "forest", "underground"],
        "key_visual_traits": [
            "robust salamander, body length is 3 times body depth",
            "glossy black body with two rows of bright yellow spots from head to tail",
            "stout body with broad flat head",
            "smooth moist skin",
            "four toes on front feet, five on hind feet",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Breeds in vernal pools; vivid yellow spots on black.",
    },
    {
        "water_type": {"lake": 2, "pond": 6, "river": 1, "stream": 3, "reservoir": 1, "marsh": 5},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 10, "slow": 5, "moderate": 1, "fast": 1},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "Tiger Salamander",
        "slug": "tiger_salamander",
        "scientific_name": "Ambystoma tigrinum",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "ponds", "grasslands", "underground"],
        "key_visual_traits": [
            "large robust salamander, body length is 3 times body depth",
            "dark brown to black body with bright yellow blotches or bars",
            "broad flat head with small eyes",
            "thick sturdy limbs and tail",
            "smooth moist skin with variable pattern",
        ],
        "relative_scale_index": 0.6,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["midwest", "mountain_west", "southwest", "northeast"],
        "notes": "Largest land-dwelling salamander in North America.",
    },
    {
        "water_type": {"lake": 3, "pond": 8, "river": 2, "stream": 3, "reservoir": 2, "marsh": 6},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 9, "slow": 5, "moderate": 2, "fast": 1},
        "vegetation": {"heavy": 5, "moderate": 6, "sparse": 6, "none": 5},
        "clarity": {"clear": 5, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Red-backed Salamander",
        "slug": "red_backed_salamander",
        "scientific_name": "Plethodon cinereus",
        "category": "amphibian",
        "habitat_tags": ["terrestrial", "forest", "under_logs", "moist_soil"],
        "key_visual_traits": [
            "small slender salamander, body length is 4 times body depth",
            "broad red or reddish-orange stripe running from head to tail",
            "dark gray to black sides and belly with white speckles",
            "costal grooves visible along the sides",
            "no aquatic larval stage (direct development)",
        ],
        "relative_scale_index": 0.2,
        "target_aspect_ratio": 4.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Most abundant salamander in eastern North America; lungless.",
    },
    {
        "water_type": {"lake": 1, "pond": 2, "river": 1, "stream": 2, "reservoir": 1, "marsh": 3},
        "depth": {"shallow": 3, "moderate": 1, "deep": 0},
        "flow": {"still": 3, "slow": 2, "moderate": 1, "fast": 0},
        "vegetation": {"heavy": 5, "moderate": 4, "sparse": 3, "none": 2},
        "clarity": {"clear": 2, "moderate": 2, "murky": 2},
    },
)

add(
    {
        "common_name": "Hellbender",
        "slug": "hellbender",
        "scientific_name": "Cryptobranchus alleganiensis",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "rivers", "streams", "rocky"],
        "key_visual_traits": [
            "very large flattened salamander, body length is 4 times body depth",
            "brown to olive-gray wrinkled flattened body",
            "flattened head with small lidless eyes",
            "loose fleshy skin folds along the sides",
            "broad flat tail for swimming",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 4.0,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Largest North American salamander; requires pristine rocky streams.",
    },
    {
        "water_type": {"lake": 1, "pond": 1, "river": 10, "stream": 10, "reservoir": 2, "marsh": 1},
        "depth": {"shallow": 5, "moderate": 8, "deep": 5},
        "flow": {"still": 1, "slow": 3, "moderate": 9, "fast": 10},
        "vegetation": {"heavy": 2, "moderate": 3, "sparse": 7, "none": 9},
        "clarity": {"clear": 10, "moderate": 6, "murky": 1},
    },
)

add(
    {
        "common_name": "Mudpuppy",
        "slug": "mudpuppy",
        "scientific_name": "Necturus maculosus",
        "category": "amphibian",
        "habitat_tags": ["freshwater", "rivers", "lakes", "streams"],
        "key_visual_traits": [
            "large aquatic salamander, body length is 3.5 times body depth",
            "gray to rusty-brown body with scattered blue-black spots",
            "prominent bushy red external gills (retained throughout life)",
            "flattened head with small eyes",
            "four toes on all feet (unlike most salamanders with five on hind)",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 3.5,
        "geographic_range": ["northeast", "midwest", "southeast"],
        "notes": "Permanently aquatic; retains larval gills as adult (neoteny).",
    },
    {
        "water_type": {"lake": 7, "pond": 4, "river": 9, "stream": 8, "reservoir": 5, "marsh": 3},
        "depth": {"shallow": 4, "moderate": 8, "deep": 6},
        "flow": {"still": 4, "slow": 6, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 7, "moderate": 7, "murky": 5},
    },
)


# ===== MAMMALS =====

add(
    {
        "common_name": "North American River Otter",
        "slug": "north_american_river_otter",
        "scientific_name": "Lontra canadensis",
        "category": "mammal",
        "habitat_tags": ["freshwater", "rivers", "lakes", "marshes"],
        "key_visual_traits": [
            "long streamlined body, body length is 3 times body depth",
            "dark brown glossy fur with lighter throat and chest",
            "long thick tapered tail",
            "small rounded ears and broad flat head",
            "webbed feet with claws and prominent whiskers",
        ],
        "relative_scale_index": 2.8,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["nationwide"],
        "notes": "Playful behavior; slides on mud and snow.",
    },
    {
        "water_type": {"lake": 8, "pond": 5, "river": 10, "stream": 8, "reservoir": 6, "marsh": 7},
        "depth": {"shallow": 6, "moderate": 8, "deep": 7},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 6},
        "vegetation": {"heavy": 5, "moderate": 7, "sparse": 7, "none": 5},
        "clarity": {"clear": 8, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "American Beaver",
        "slug": "american_beaver",
        "scientific_name": "Castor canadensis",
        "category": "mammal",
        "habitat_tags": ["freshwater", "rivers", "streams", "ponds"],
        "key_visual_traits": [
            "large rodent with stocky body, body length is 1.5 times body depth",
            "dark brown dense waterproof fur",
            "broad flat scaly paddle-shaped tail",
            "large orange incisors for gnawing wood",
            "webbed hind feet and small rounded ears",
        ],
        "relative_scale_index": 2.5,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Ecosystem engineer; builds dams and lodges from branches.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 8, "stream": 10, "reservoir": 5, "marsh": 6},
        "depth": {"shallow": 6, "moderate": 8, "deep": 5},
        "flow": {"still": 6, "slow": 8, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Muskrat",
        "slug": "muskrat",
        "scientific_name": "Ondatra zibethicus",
        "category": "mammal",
        "habitat_tags": ["freshwater", "marshes", "ponds", "rivers"],
        "key_visual_traits": [
            "medium semi-aquatic rodent, body length is 2 times body depth",
            "dense brown fur with darker back",
            "long scaly laterally flattened tail",
            "partially webbed hind feet",
            "small eyes and short rounded ears",
        ],
        "relative_scale_index": 1.0,
        "target_aspect_ratio": 2.0,
        "geographic_range": ["nationwide"],
        "notes": "Builds lodges from cattails; tail is vertically flattened (not flat like beaver).",
    },
    {
        "water_type": {"lake": 5, "pond": 8, "river": 5, "stream": 4, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 10, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 5, "moderate": 7, "murky": 7},
    },
)

add(
    {
        "common_name": "American Mink",
        "slug": "american_mink",
        "scientific_name": "Neogale vison",
        "category": "mammal",
        "habitat_tags": ["freshwater", "rivers", "streams", "marshes"],
        "key_visual_traits": [
            "small sleek weasel-like body, body length is 3 times body depth",
            "glossy dark brown to nearly black fur",
            "small white chin patch",
            "short legs with partially webbed feet",
            "bushy tail about one-third of body length",
        ],
        "relative_scale_index": 1.2,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["nationwide"],
        "notes": "Aggressive semi-aquatic predator; luxurious dense fur.",
    },
    {
        "water_type": {"lake": 5, "pond": 6, "river": 8, "stream": 9, "reservoir": 4, "marsh": 8},
        "depth": {"shallow": 8, "moderate": 6, "deep": 3},
        "flow": {"still": 5, "slow": 7, "moderate": 8, "fast": 5},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 6, "none": 4},
        "clarity": {"clear": 6, "moderate": 7, "murky": 6},
    },
)

add(
    {
        "common_name": "Nutria",
        "slug": "nutria",
        "scientific_name": "Myocastor coypus",
        "category": "mammal",
        "habitat_tags": ["freshwater", "marshes", "rivers", "canals"],
        "key_visual_traits": [
            "large robust rodent, body length is 1.5 times body depth",
            "coarse brown outer fur with dense gray underfur",
            "bright orange incisors",
            "long round rat-like tail (not flat like beaver or muskrat)",
            "webbed hind feet and prominent white whiskers",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["southeast", "southwest", "pacific_northwest"],
        "notes": "Invasive South American species; destructive to wetlands.",
    },
    {
        "water_type": {"lake": 4, "pond": 6, "river": 6, "stream": 3, "reservoir": 4, "marsh": 10},
        "depth": {"shallow": 9, "moderate": 5, "deep": 2},
        "flow": {"still": 8, "slow": 7, "moderate": 4, "fast": 2},
        "vegetation": {"heavy": 10, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 4, "moderate": 6, "murky": 8},
    },
)

add(
    {
        "common_name": "Water Shrew",
        "slug": "water_shrew",
        "scientific_name": "Sorex palustris",
        "category": "mammal",
        "habitat_tags": ["freshwater", "streams", "rivers", "cold_water"],
        "key_visual_traits": [
            "tiny elongated body, body length is 3 times body depth",
            "dark velvety fur on top, silvery belly",
            "fringed hind feet with stiff hairs for swimming",
            "long pointed snout with whiskers",
            "tiny eyes and hidden ears",
        ],
        "relative_scale_index": 0.2,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["northeast", "mountain_west", "pacific_northwest", "alaska"],
        "notes": "Can run on water surface briefly due to air-trapping foot hairs.",
    },
    {
        "water_type": {"lake": 3, "pond": 3, "river": 7, "stream": 10, "reservoir": 2, "marsh": 4},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 3, "slow": 5, "moderate": 9, "fast": 8},
        "vegetation": {"heavy": 4, "moderate": 6, "sparse": 7, "none": 6},
        "clarity": {"clear": 9, "moderate": 6, "murky": 3},
    },
)

add(
    {
        "common_name": "Star-nosed Mole",
        "slug": "star_nosed_mole",
        "scientific_name": "Condylura cristata",
        "category": "mammal",
        "habitat_tags": ["freshwater", "marshes", "wet_meadows", "streams"],
        "key_visual_traits": [
            "small stout body with enormous shovel-like forefeet, body length is 1.5 times body depth",
            "distinctive ring of 22 pink fleshy tentacles on the nose",
            "dark brown to black velvety fur",
            "long thick scaly tail (stores fat in winter)",
            "large broad forefeet for digging",
        ],
        "relative_scale_index": 0.3,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Fastest-eating mammal; nose tentacles are touch-sensitive organs.",
    },
    {
        "water_type": {"lake": 2, "pond": 5, "river": 3, "stream": 6, "reservoir": 2, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 7, "slow": 7, "moderate": 5, "fast": 3},
        "vegetation": {"heavy": 7, "moderate": 7, "sparse": 5, "none": 3},
        "clarity": {"clear": 4, "moderate": 6, "murky": 7},
    },
)

add(
    {
        "common_name": "American Water Shrew",
        "slug": "american_water_shrew",
        "scientific_name": "Sorex palustris navigator",
        "category": "mammal",
        "habitat_tags": ["freshwater", "mountain_streams", "cold_water"],
        "key_visual_traits": [
            "tiny dark-furred body, body length is 3 times body depth",
            "bicolored with dark back and silver-white belly",
            "hind feet fringed with stiff hairs for propulsion",
            "long whiskers and pointed snout",
            "keeled tail for steering while swimming",
        ],
        "relative_scale_index": 0.2,
        "target_aspect_ratio": 3.0,
        "geographic_range": ["mountain_west", "pacific_northwest", "northeast"],
        "notes": "Subspecies of the water shrew adapted to mountain streams.",
    },
    {
        "water_type": {"lake": 2, "pond": 3, "river": 6, "stream": 10, "reservoir": 2, "marsh": 3},
        "depth": {"shallow": 10, "moderate": 5, "deep": 2},
        "flow": {"still": 2, "slow": 4, "moderate": 8, "fast": 9},
        "vegetation": {"heavy": 3, "moderate": 5, "sparse": 7, "none": 7},
        "clarity": {"clear": 10, "moderate": 6, "murky": 2},
    },
)


# ===== PLANTS =====

add(
    {
        "common_name": "Duckweed",
        "slug": "duckweed",
        "scientific_name": "Lemna minor",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "marshes", "still_water"],
        "key_visual_traits": [
            "tiny floating oval green leaves covering the water surface",
            "each leaf (frond) is 2-5mm across with a single root trailing below",
            "forms dense green carpet on still water surface",
            "flat horizontal composition, width is 2-3 times height",
        ],
        "relative_scale_index": 0.05,
        "target_aspect_ratio": 1.5,
        "geographic_range": ["nationwide"],
        "notes": "Smallest flowering plant; important duck food source.",
    },
    {
        "water_type": {"lake": 6, "pond": 10, "river": 2, "stream": 1, "reservoir": 5, "marsh": 8},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 10, "slow": 5, "moderate": 1, "fast": 0},
        "vegetation": {"heavy": 10, "moderate": 6, "sparse": 2, "none": 1},
        "clarity": {"clear": 5, "moderate": 6, "murky": 6},
    },
)

add(
    {
        "common_name": "Pickerelweed",
        "slug": "pickerelweed",
        "scientific_name": "Pontederia cordata",
        "category": "plant",
        "habitat_tags": ["freshwater", "marshes", "ponds", "shorelines"],
        "key_visual_traits": [
            "emergent aquatic plant with tall stems, height is 3-4 times width",
            "large heart-shaped glossy green leaves on long stems",
            "spike of blue-violet flowers at top of stem",
            "grows in shallow water along shorelines",
        ],
        "relative_scale_index": 1.5,
        "target_aspect_ratio": 0.4,
        "geographic_range": ["northeast", "southeast", "midwest"],
        "notes": "Named for growing alongside chain pickerel habitat.",
    },
    {
        "water_type": {"lake": 6, "pond": 9, "river": 4, "stream": 3, "reservoir": 4, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 6, "moderate": 7, "murky": 5},
    },
)

add(
    {
        "common_name": "American Lotus",
        "slug": "american_lotus",
        "scientific_name": "Nelumbo lutea",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "lakes", "slow_rivers"],
        "key_visual_traits": [
            "large emergent aquatic plant with circular leaves, width is 1.2 times height",
            "large round blue-green leaves held above water like parasols",
            "large pale yellow flower with distinctive cone-shaped seed pod",
            "leaves are circular without the notch of water lilies",
        ],
        "relative_scale_index": 0.8,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["southeast", "midwest", "northeast"],
        "notes": "Only native lotus in North America; leaves repel water.",
    },
    {
        "water_type": {"lake": 8, "pond": 9, "river": 5, "stream": 2, "reservoir": 5, "marsh": 6},
        "depth": {"shallow": 9, "moderate": 6, "deep": 2},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 7, "moderate": 7, "murky": 4},
    },
)

add(
    {
        "common_name": "Blue Flag Iris",
        "slug": "blue_flag_iris",
        "scientific_name": "Iris versicolor",
        "category": "plant",
        "habitat_tags": ["freshwater", "marshes", "wet_meadows", "shorelines"],
        "key_visual_traits": [
            "tall upright stems with sword-shaped leaves, height is 4-5 times width",
            "showy blue-violet flowers with yellow and white markings",
            "flat sword-like leaves in a fan arrangement",
            "grows in clumps along water edges",
        ],
        "relative_scale_index": 1.0,
        "target_aspect_ratio": 0.35,
        "geographic_range": ["northeast", "midwest", "southeast"],
        "notes": "Native iris; attractive flowering wetland plant.",
    },
    {
        "water_type": {"lake": 4, "pond": 7, "river": 3, "stream": 3, "reservoir": 3, "marsh": 9},
        "depth": {"shallow": 10, "moderate": 3, "deep": 1},
        "flow": {"still": 9, "slow": 7, "moderate": 3, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 5, "moderate": 6, "murky": 6},
    },
)

add(
    {
        "common_name": "Water Hyacinth",
        "slug": "water_hyacinth",
        "scientific_name": "Pontederia crassipes",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "lakes", "slow_rivers"],
        "key_visual_traits": [
            "floating rosette plant, width is 1.2 times height",
            "glossy rounded leaves with swollen bladder-like petioles",
            "showy lavender-blue flower spike with yellow spot",
            "thick fibrous trailing root mass",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.0,
        "geographic_range": ["southeast", "southwest", "california"],
        "notes": "Invasive; clogs waterways. One of the worst aquatic weeds globally.",
    },
    {
        "water_type": {"lake": 7, "pond": 9, "river": 5, "stream": 2, "reservoir": 6, "marsh": 7},
        "depth": {"shallow": 10, "moderate": 4, "deep": 1},
        "flow": {"still": 10, "slow": 7, "moderate": 2, "fast": 0},
        "vegetation": {"heavy": 10, "moderate": 6, "sparse": 2, "none": 1},
        "clarity": {"clear": 4, "moderate": 6, "murky": 7},
    },
)

add(
    {
        "common_name": "Elodea",
        "slug": "elodea",
        "scientific_name": "Elodea canadensis",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "lakes", "streams"],
        "key_visual_traits": [
            "submerged aquatic plant with whorled leaves, vertical stems, height is 5-6 times width",
            "small dark green leaves in whorls of three along branching stems",
            "dense underwater growth forming thick beds",
            "tiny white three-petaled flowers at the surface",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 0.4,
        "geographic_range": ["nationwide"],
        "notes": "Common aquarium plant; important oxygenator in ponds.",
    },
    {
        "water_type": {"lake": 8, "pond": 9, "river": 5, "stream": 6, "reservoir": 6, "marsh": 4},
        "depth": {"shallow": 8, "moderate": 8, "deep": 3},
        "flow": {"still": 8, "slow": 8, "moderate": 5, "fast": 2},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 9, "moderate": 6, "murky": 2},
    },
)

add(
    {
        "common_name": "Coontail",
        "slug": "coontail",
        "scientific_name": "Ceratophyllum demersum",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "lakes", "slow_rivers"],
        "key_visual_traits": [
            "submerged rootless aquatic plant, height is 4-5 times width",
            "stiff dark green forked leaves in dense whorls resembling a raccoon tail",
            "no true roots; free-floating or loosely anchored",
            "bushy appearance with leaves denser near the growing tip",
        ],
        "relative_scale_index": 0.6,
        "target_aspect_ratio": 0.4,
        "geographic_range": ["nationwide"],
        "notes": "Rootless; floats freely; important fish habitat.",
    },
    {
        "water_type": {"lake": 8, "pond": 9, "river": 5, "stream": 4, "reservoir": 6, "marsh": 6},
        "depth": {"shallow": 7, "moderate": 8, "deep": 4},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 1},
        "vegetation": {"heavy": 9, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 8, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "Pondweed",
        "slug": "pondweed",
        "scientific_name": "Potamogeton natans",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "lakes", "slow_streams"],
        "key_visual_traits": [
            "aquatic plant with floating oval leaves, width is 1.5 times height",
            "oval to elliptical floating leaves on long flexible stems",
            "submerged leaves are thin and translucent",
            "small greenish flower spike emerging above water",
        ],
        "relative_scale_index": 0.5,
        "target_aspect_ratio": 1.2,
        "geographic_range": ["nationwide"],
        "notes": "Important waterfowl food; floating and submerged leaf forms.",
    },
    {
        "water_type": {"lake": 8, "pond": 9, "river": 4, "stream": 5, "reservoir": 6, "marsh": 5},
        "depth": {"shallow": 8, "moderate": 7, "deep": 3},
        "flow": {"still": 9, "slow": 7, "moderate": 4, "fast": 1},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 8, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "Water Milfoil",
        "slug": "water_milfoil",
        "scientific_name": "Myriophyllum spicatum",
        "category": "plant",
        "habitat_tags": ["freshwater", "lakes", "ponds", "rivers"],
        "key_visual_traits": [
            "submerged plant with feathery whorled leaves, height is 5-6 times width",
            "finely divided feather-like leaves in whorls of four",
            "reddish-green stems reaching up to the surface",
            "small reddish flower spikes above the water",
        ],
        "relative_scale_index": 0.6,
        "target_aspect_ratio": 0.4,
        "geographic_range": ["nationwide"],
        "notes": "Invasive Eurasian species; fragments easily to spread.",
    },
    {
        "water_type": {"lake": 9, "pond": 8, "river": 5, "stream": 4, "reservoir": 7, "marsh": 5},
        "depth": {"shallow": 7, "moderate": 9, "deep": 4},
        "flow": {"still": 8, "slow": 7, "moderate": 4, "fast": 1},
        "vegetation": {"heavy": 10, "moderate": 7, "sparse": 3, "none": 1},
        "clarity": {"clear": 9, "moderate": 7, "murky": 3},
    },
)

add(
    {
        "common_name": "Bladderwort",
        "slug": "bladderwort",
        "scientific_name": "Utricularia vulgaris",
        "category": "plant",
        "habitat_tags": ["freshwater", "ponds", "marshes", "bogs"],
        "key_visual_traits": [
            "submerged rootless carnivorous plant, height is 3-4 times width",
            "finely divided feathery leaves with tiny bladder-like traps",
            "small bright yellow snapdragon-like flowers on emergent stalks",
            "free-floating without roots",
        ],
        "relative_scale_index": 0.4,
        "target_aspect_ratio": 0.5,
        "geographic_range": ["nationwide"],
        "notes": "Carnivorous; bladder traps capture tiny aquatic organisms.",
    },
    {
        "water_type": {"lake": 5, "pond": 9, "river": 2, "stream": 3, "reservoir": 4, "marsh": 8},
        "depth": {"shallow": 9, "moderate": 5, "deep": 1},
        "flow": {"still": 10, "slow": 6, "moderate": 2, "fast": 0},
        "vegetation": {"heavy": 8, "moderate": 7, "sparse": 4, "none": 2},
        "clarity": {"clear": 7, "moderate": 6, "murky": 4},
    },
)


# ---------------------------------------------------------------------------
# Merge and write
# ---------------------------------------------------------------------------

all_species = existing + [sp for sp in new_species if sp["slug"] not in existing_slugs]

# Add geographic_range to existing species that lack it
for sp in all_species:
    if "geographic_range" not in sp:
        slug = sp["slug"]
        # Assign reasonable ranges for existing species
        geo_defaults = {
            "smallmouth_bass": ["northeast", "midwest", "southeast", "mountain_west"],
            "largemouth_bass": ["nationwide"],
            "northern_pike": ["northeast", "midwest", "mountain_west", "alaska"],
            "bowfin": ["northeast", "southeast", "midwest"],
            "bluegill": ["nationwide"],
            "black_crappie": ["nationwide"],
            "rock_bass": ["northeast", "midwest"],
            "pumpkinseed_sunfish": ["northeast", "midwest"],
            "yellow_perch": ["northeast", "midwest", "mountain_west"],
            "channel_catfish": ["nationwide"],
            "bullhead_catfish": ["nationwide"],
            "common_carp": ["nationwide"],
            "white_sucker": ["northeast", "midwest", "mountain_west"],
            "painted_turtle": ["nationwide"],
            "red_eared_slider": ["southeast", "midwest", "southwest"],
            "common_snapping_turtle": ["northeast", "southeast", "midwest"],
            "eastern_box_turtle": ["northeast", "southeast"],
            "spiny_softshell_turtle": ["midwest", "southeast", "northeast"],
            "great_blue_heron": ["nationwide"],
            "belted_kingfisher": ["nationwide"],
            "common_loon": ["northeast", "midwest", "mountain_west", "pacific_northwest", "alaska"],
            "osprey": ["nationwide"],
            "mallard": ["nationwide"],
            "wood_duck": ["northeast", "southeast", "midwest", "pacific_northwest"],
            "american_bittern": ["northeast", "midwest", "mountain_west", "pacific_northwest"],
            "cattails": ["nationwide"],
            "water_lily": ["northeast", "southeast", "midwest"],
            "bulrush": ["nationwide"],
            "wild_rice": ["northeast", "midwest"],
            "arrowhead": ["nationwide"],
        }
        sp["geographic_range"] = geo_defaults.get(slug, ["nationwide"])

# Write species.json
species_path = DATA_DIR / "species" / "species.json"
species_path.parent.mkdir(parents=True, exist_ok=True)
with open(species_path, "w") as f:
    json.dump(all_species, f, indent=2)
    f.write("\n")

# Build habitat profiles (keep existing, add new)
all_profiles = dict(existing_profiles)
all_profiles.update(new_profiles)

profiles_path = DATA_DIR / "species" / "habitat_profiles.json"
with open(profiles_path, "w") as f:
    json.dump(all_profiles, f, indent=2)
    f.write("\n")

# Write regions.json
regions_path = DATA_DIR / "regions.json"
with open(regions_path, "w") as f:
    json.dump(REGIONS, f, indent=2)
    f.write("\n")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"Total species: {len(all_species)}")
print()

from collections import Counter
cats = Counter(sp["category"] for sp in all_species)
for cat, count in sorted(cats.items()):
    print(f"  {cat}: {count}")

print()
print(f"Habitat profiles: {len(all_profiles)}")
print(f"Regions: {len(REGIONS)}")

# Verify all species have profiles
species_slugs = {sp["slug"] for sp in all_species}
profile_slugs = set(all_profiles.keys())
missing = species_slugs - profile_slugs
extra = profile_slugs - species_slugs
if missing:
    print(f"\nWARNING: Species without profiles: {missing}")
if extra:
    print(f"\nWARNING: Profiles without species: {extra}")
if not missing and not extra:
    print("\nAll species have matching habitat profiles.")

# Geographic coverage
all_regions_used = set()
for sp in all_species:
    for r in sp.get("geographic_range", []):
        all_regions_used.add(r)
print(f"\nGeographic regions covered: {sorted(all_regions_used)}")
print(f"Region count: {len(all_regions_used)}")
