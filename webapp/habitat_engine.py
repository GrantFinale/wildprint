"""Habitat-based species recommendation engine.

Scores each species against user-provided habitat characteristics and
returns a ranked list split into primary (top N) and secondary
recommendations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import PROJECT_ROOT, SPECIES_JSON


logger = logging.getLogger(__name__)

_HABITAT_PROFILES_PATH = Path(PROJECT_ROOT) / "data" / "species" / "habitat_profiles.json"
_REGIONS_PATH = Path(PROJECT_ROOT) / "data" / "regions.json"


def load_habitat_profiles() -> dict:
    """Read habitat_profiles.json and return the dict keyed by species slug."""
    with open(_HABITAT_PROFILES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_regions() -> dict:
    """Read regions.json and return the dict mapping region slug to state lists."""
    with open(_REGIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def state_to_region(state_code: str) -> str | None:
    """Map a 2-letter state code to a region slug using regions.json.

    Returns None if the state code is not found in any region.
    """
    regions = load_regions()
    upper = state_code.upper()
    for region_slug, states in regions.items():
        if upper in states:
            return region_slug
    return None


def score_species(profiles: dict, answers: dict) -> list[tuple[str, float]]:
    """Score every species against the user's habitat answers.

    Parameters
    ----------
    profiles:
        The full habitat profiles dict (slug -> dimension -> choice -> score).
    answers:
        User answers, e.g. {"water_type": "lake", "depth": "shallow", ...}.

    Returns
    -------
    Sorted list of (slug, total_score) descending.
    """
    results: list[tuple[str, float]] = []
    for slug, dimensions in profiles.items():
        total = 0.0
        for dimension, choice in answers.items():
            dim_scores = dimensions.get(dimension, {})
            total += dim_scores.get(choice, 0)
        results.append((slug, total))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


# Per-water-type category quotas for the primary list.
# Goal: surface the species a user actually expects for that habitat —
# fish-heavy for lakes/rivers, balanced for marshes, bird-heavy for ponds.
_PRIMARY_QUOTAS: dict[str, dict[str, int]] = {
    "lake":      {"fish": 6, "bird": 2, "turtle": 1, "amphibian": 1, "reptile": 0, "mammal": 0},
    "pond":      {"fish": 5, "bird": 1, "turtle": 1, "amphibian": 2, "reptile": 0, "mammal": 1},
    "river":     {"fish": 7, "bird": 1, "turtle": 1, "amphibian": 0, "reptile": 1, "mammal": 0},
    "stream":    {"fish": 6, "bird": 1, "turtle": 0, "amphibian": 2, "reptile": 0, "mammal": 1},
    "reservoir": {"fish": 7, "bird": 2, "turtle": 1, "amphibian": 0, "reptile": 0, "mammal": 0},
    "marsh":     {"fish": 3, "bird": 4, "turtle": 1, "amphibian": 1, "reptile": 1, "mammal": 0},
}
_DEFAULT_QUOTA = {"fish": 5, "bird": 2, "turtle": 1, "amphibian": 1, "reptile": 0, "mammal": 1}

# How much each commonness level boosts the raw habitat score. Tuned so a
# very-common species at habitat-fit 35 beats a rare specialist at 42.
_COMMONNESS_BOOST = {3: 8, 2: 4, 1: 0, 0: -3}


def recommend(
    answers: dict,
    primary_count: int = 10,
    secondary_count: int = 20,
    region: str | None = None,
    categories: list[str] | None = None,
    offset: int = 0,
) -> dict:
    """Return primary and secondary species recommendations.

    Algorithm:
      1. Score every species' habitat fit across 5 dimensions.
      2. Apply commonness boost so abundant species win ties.
      3. Filter out plants (they're decorative borders).
      4. Filter by geographic region (state -> region).
      5. Filter by selected categories if provided.
      6. Compose primary list using category quotas tuned to water_type,
         so users see the right MIX (fish for lakes, birds for marshes).
      7. Secondary list = next high-scoring species across all categories,
         starting at ``offset`` (for "Show 20 more" pagination).

    Behavior with ``categories``:
      - None or empty: all categories considered (legacy behavior).
      - Length 1: ``primary_count`` is forced to 15 (focused field guide).
      - Length >= 2: per-water-type quotas are LIMITED to the selected
        categories, summed; if that falls short of ``primary_count``,
        we fill with the next-best species in the selection.

    Tiebreakers: commonness desc, then scientific_name asc (deterministic).
    """
    profiles = load_habitat_profiles()
    scored = score_species(profiles, answers)

    with open(SPECIES_JSON, "r", encoding="utf-8") as f:
        all_species = json.load(f)
    species_by_slug = {sp["slug"]: sp for sp in all_species}

    # --- Normalize the categories filter ---
    cat_filter: set[str] | None = None
    if categories:
        cat_filter = {c.lower() for c in categories if c}
        if not cat_filter:
            cat_filter = None

    # If exactly one category is selected, treat this as a focused field
    # guide and bump the primary list to 15.
    if cat_filter is not None and len(cat_filter) == 1:
        primary_count = 15

    # --- Apply commonness boost (and drop plants + non-selected cats) ---
    boosted: list[tuple[str, float, int]] = []
    for slug, raw_score in scored:
        sp = species_by_slug.get(slug, {})
        cat = sp.get("category")
        if cat == "plant":
            continue
        if cat_filter is not None and cat not in cat_filter:
            continue
        commonness = int(sp.get("commonness", 1))
        boost = _COMMONNESS_BOOST.get(commonness, 0)
        adj_score = raw_score + boost
        boosted.append((slug, adj_score, commonness))

    # --- Geographic filter ---
    if region:
        region_lower = region.lower()

        def _in_region(slug: str) -> bool:
            sp = species_by_slug.get(slug, {})
            geo = [g.lower() for g in sp.get("geographic_range", [])]
            return region_lower in geo or "nationwide" in geo

        before = len(boosted)
        boosted = [b for b in boosted if _in_region(b[0])]
        logger.info(
            "Geographic filter region=%s: %d -> %d species",
            region, before, len(boosted),
        )

    # --- Sort: score desc, commonness desc, scientific_name asc ---
    def _sort_key(item):
        slug, score, commonness = item
        sp = species_by_slug.get(slug, {})
        return (-score, -commonness, sp.get("scientific_name", "") or slug)

    boosted.sort(key=_sort_key)

    # --- Group by category for quota assembly ---
    by_category: dict[str, list[tuple[str, float, int]]] = {}
    for item in boosted:
        cat = species_by_slug.get(item[0], {}).get("category", "other")
        by_category.setdefault(cat, []).append(item)

    # --- Compose primary list using per-water-type quotas ---
    water_type = (answers.get("water_type") or "lake").lower()
    base_quota = _PRIMARY_QUOTAS.get(water_type, _DEFAULT_QUOTA)
    # Restrict quotas to the user-selected categories, if any.
    if cat_filter is not None:
        quota = {k: v for k, v in base_quota.items() if k in cat_filter}
    else:
        quota = dict(base_quota)

    primary_picks: list[tuple[str, float, int]] = []
    used_slugs: set[str] = set()
    for cat, n in quota.items():
        if n <= 0:
            continue
        for item in by_category.get(cat, [])[:n]:
            primary_picks.append(item)
            used_slugs.add(item[0])

    # If quota fell short of primary_count, top up with next-best across all
    # remaining (already filtered to cat_filter if present).
    if len(primary_picks) < primary_count:
        for item in boosted:
            if item[0] in used_slugs:
                continue
            primary_picks.append(item)
            used_slugs.add(item[0])
            if len(primary_picks) >= primary_count:
                break

    primary_picks.sort(key=_sort_key)
    primary_picks = primary_picks[:primary_count]
    primary_slug_set = {p[0] for p in primary_picks}

    # --- Secondary: next high-scoring species not already in primary ---
    post_primary = [b for b in boosted if b[0] not in primary_slug_set]
    if offset < 0:
        offset = 0
    secondary_picks = post_primary[offset:offset + secondary_count]
    total_remaining = max(0, len(post_primary) - (offset + len(secondary_picks)))

    def _enrich(item):
        slug, score, _commonness = item
        sp = species_by_slug.get(slug, {})
        return {
            "slug": slug,
            "score": round(score, 1),
            "common_name": sp.get("common_name", slug),
            "scientific_name": sp.get("scientific_name", ""),
            "category": sp.get("category", ""),
            "commonness": int(sp.get("commonness", 1)),
        }

    return {
        "primary": [_enrich(p) for p in primary_picks],
        "secondary": [_enrich(s) for s in secondary_picks],
        "all_scores": [_enrich(b) for b in boosted],
        "total_remaining": total_remaining,
    }


def get_species_by_slugs(slugs: list[str]) -> list[dict]:
    """Read species.json and return records matching the given slugs."""
    with open(SPECIES_JSON, "r", encoding="utf-8") as f:
        all_species = json.load(f)
    slug_set = set(slugs)
    return [sp for sp in all_species if sp.get("slug") in slug_set]
