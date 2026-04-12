"""Habitat-based species recommendation engine.

Scores each species against user-provided habitat characteristics and
returns a ranked list split into primary (top N) and secondary
recommendations.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.settings import PROJECT_ROOT, SPECIES_JSON


_HABITAT_PROFILES_PATH = Path(PROJECT_ROOT) / "data" / "species" / "habitat_profiles.json"


def load_habitat_profiles() -> dict:
    """Read habitat_profiles.json and return the dict keyed by species slug."""
    with open(_HABITAT_PROFILES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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


def recommend(
    answers: dict,
    primary_count: int = 10,
    secondary_count: int = 8,
) -> dict:
    """Return primary and secondary species recommendations.

    Filters out category='plant' species from recommendations (plants are
    border decorations handled automatically by the renderer).

    Returns
    -------
    Dict with keys 'primary', 'secondary', 'all_scores'. Each entry in
    primary/secondary is {"slug": str, "score": float, ...species fields}.
    """
    profiles = load_habitat_profiles()
    scored = score_species(profiles, answers)

    # Load species records for enrichment and plant filtering
    with open(SPECIES_JSON, "r", encoding="utf-8") as f:
        all_species = json.load(f)
    species_by_slug = {sp["slug"]: sp for sp in all_species}

    # Filter out plants
    scored_filtered = [
        (slug, score)
        for slug, score in scored
        if species_by_slug.get(slug, {}).get("category") != "plant"
    ]

    def _enrich(slug: str, score: float) -> dict:
        sp = species_by_slug.get(slug, {})
        return {
            "slug": slug,
            "score": score,
            "common_name": sp.get("common_name", slug),
            "scientific_name": sp.get("scientific_name", ""),
            "category": sp.get("category", ""),
        }

    primary = [_enrich(s, sc) for s, sc in scored_filtered[:primary_count]]
    secondary = [
        _enrich(s, sc)
        for s, sc in scored_filtered[primary_count : primary_count + secondary_count]
    ]
    all_scores = [_enrich(s, sc) for s, sc in scored_filtered]

    return {"primary": primary, "secondary": secondary, "all_scores": all_scores}


def get_species_by_slugs(slugs: list[str]) -> list[dict]:
    """Read species.json and return records matching the given slugs."""
    with open(SPECIES_JSON, "r", encoding="utf-8") as f:
        all_species = json.load(f)
    slug_set = set(slugs)
    return [sp for sp in all_species if sp.get("slug") in slug_set]
