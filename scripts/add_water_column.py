"""One-shot script: add water_column to every enabled fish in species.json.

Biologically accurate mapping per agent task spec, plus careful slug-name
inference for slugs not in the listed sets.
"""
from __future__ import annotations

import json
from pathlib import Path

# --- Authoritative biological mapping (from task spec) ----------------------
TOP_SLUGS = {
    # Pike & pickerel — ambush predators in the upper column.
    "pike", "northern_pike", "muskellunge", "tiger_muskellunge",
    "tiger_musky",  # alt slug used in this species.json
    "chain_pickerel", "redfin_pickerel", "grass_pickerel",
    # Bowfin — surface-air gulper, top.
    "bowfin",
    # Gars — surface-oriented obligate air-breathers.
    "longnose_gar", "shortnose_gar", "alligator_gar",
    "spotted_gar", "florida_gar",
    # Salmonids — cool-water cruisers in the upper column.
    "brook_trout", "brown_trout", "rainbow_trout", "lake_trout",
    "cutthroat_trout",
    "atlantic_salmon", "chinook_salmon", "coho_salmon", "sockeye_salmon",
    "kokanee", "lake_whitefish", "cisco",
    # Striper complex — pelagic top-water.
    "striped_bass", "white_bass", "hybrid_striped_bass",
}

MID_SLUGS = {
    # Black basses.
    "largemouth_bass", "smallmouth_bass", "spotted_bass",
    "redeye_bass", "guadalupe_bass",
    # Sunfish.
    "bluegill", "pumpkinseed", "pumpkinseed_sunfish",
    "redear_sunfish", "redbreast_sunfish", "green_sunfish",
    "longear_sunfish", "warmouth", "rock_bass",
    # Crappies.
    "black_crappie", "white_crappie",
    # Perch / walleye complex.
    "yellow_perch", "walleye", "sauger", "saugeye",
    "white_perch",
    # Cyprinids — small column dwellers.
    "golden_shiner", "common_shiner", "emerald_shiner",
    "fallfish", "creek_chub",
    # Other mid.
    "bluefish", "freshwater_drum",
}

BOTTOM_SLUGS = {
    # Ictalurids — catfish.
    "channel_catfish", "blue_catfish", "flathead_catfish",
    "white_catfish",
    "black_bullhead", "brown_bullhead", "yellow_bullhead",
    "bullhead_catfish",
    # Carps.
    "common_carp", "mirror_carp", "grass_carp",
    "bighead_carp", "silver_carp",
    # Suckers / redhorses.
    "white_sucker", "longnose_sucker",
    "blacktail_redhorse", "shorthead_redhorse",
    "river_redhorse", "golden_redhorse", "silver_redhorse",
    # Buffalo.
    "bigmouth_buffalo", "smallmouth_buffalo", "black_buffalo",
    # Sturgeon family.
    "lake_sturgeon", "white_sturgeon", "shovelnose_sturgeon",
    # Other bottom.
    "american_eel", "paddlefish",
}

# --- Inference for slugs NOT in the authoritative lists --------------------
# (Use biology + slug name. Lakes-of-N.America species in this file that
# weren't enumerated above:)
#   atlantic_sturgeon, pallid_sturgeon — sturgeon → BOTTOM (substrate)
#   bull_trout, apache_trout — char/trout salmonids → TOP
#   pink_salmon, chum_salmon — Pacific salmon → TOP
#   american_shad, gizzard_shad, hickory_shad — pelagic schoolers,
#     surface-feeding clupeids → TOP
#   mooneye, goldeye — surface-insect feeders (Hiodontidae) → TOP
#   johnny_darter, rainbow_darter — benthic substrate-clingers → BOTTOM
#   fathead_minnow — column dweller → MID
#   banded_killifish, mummichog — surface-skimming top-minnows → TOP
INFERRED = {
    "atlantic_sturgeon": "bottom",
    "pallid_sturgeon": "bottom",
    "bull_trout": "top",
    "apache_trout": "top",
    "pink_salmon": "top",
    "chum_salmon": "top",
    "american_shad": "top",
    "gizzard_shad": "top",
    "hickory_shad": "top",
    "mooneye": "top",
    "goldeye": "top",
    "johnny_darter": "bottom",
    "rainbow_darter": "bottom",
    "fathead_minnow": "mid",
    "banded_killifish": "top",
    "mummichog": "top",
}


def classify(slug: str) -> tuple[str, bool]:
    """Return (water_column, was_inferred). was_inferred=True when slug
    wasn't in the authoritative lists.
    """
    if slug in TOP_SLUGS:
        return "top", False
    if slug in MID_SLUGS:
        return "mid", False
    if slug in BOTTOM_SLUGS:
        return "bottom", False
    if slug in INFERRED:
        return INFERRED[slug], True
    return "mid", True


def main() -> None:
    path = Path("data/species/species.json")
    with path.open() as f:
        data = json.load(f)

    n_fish, n_top, n_mid, n_bot, n_inferred = 0, 0, 0, 0, 0
    for entry in data:
        if entry.get("category") != "fish":
            continue
        if not entry.get("enabled"):
            continue
        slug = entry["slug"]
        wc, inferred = classify(slug)
        entry["water_column"] = wc
        if inferred:
            entry["_water_column_inferred"] = True
            n_inferred += 1
        else:
            entry.pop("_water_column_inferred", None)
        n_fish += 1
        if wc == "top":
            n_top += 1
        elif wc == "mid":
            n_mid += 1
        else:
            n_bot += 1

    with path.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(
        f"updated {n_fish} fish: top={n_top}, mid={n_mid}, bottom={n_bot}, "
        f"inferred={n_inferred}"
    )


if __name__ == "__main__":
    main()
