"""Build image generation prompts from species and style data.

Importable module and runnable CLI. Run from project root with
`PYTHONPATH=.` set, e.g.:

    python -m scripts.generate_prompts --species bluegill --style woodcut
"""
from __future__ import annotations


import argparse
import json
from typing import Iterator

from config.settings import (
    BASE_PROMPT_BY_CATEGORY,
    BASE_PROMPT_PATH,
    SPECIES_JSON,
    STYLES_JSON,
)


def load_species() -> list[dict]:
    """Load and return the species records from SPECIES_JSON."""
    with open(SPECIES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_styles() -> list[dict]:
    """Load and return the style records from STYLES_JSON."""
    with open(STYLES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_base_prompt(category: str | None = None) -> str:
    """Load and return the base prompt template string.

    If ``category`` is provided and matches a key in
    ``BASE_PROMPT_BY_CATEGORY`` (e.g. ``"fish"``, ``"turtle"``, ``"bird"``),
    the category-specific template file is loaded. Otherwise the default
    ``BASE_PROMPT_PATH`` template is used as a fallback.
    """
    path = BASE_PROMPT_PATH
    if category is not None:
        path = BASE_PROMPT_BY_CATEGORY.get(category, BASE_PROMPT_PATH)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_prompt(
    species: dict, style: dict, base_prompt: str | None = None
) -> str:
    """Fill the base prompt template with species and style data.

    Args:
        species: A species record dict.
        style: A style record dict.
        base_prompt: Optional pre-loaded base prompt template. If None,
            the template is loaded from disk.

    Returns:
        The final prompt string with placeholders substituted.
    """
    if base_prompt is None:
        base_prompt = load_base_prompt(species.get("category"))

    traits = ", ".join(species.get("key_visual_traits", []))
    return base_prompt.format(
        species_name=species["common_name"],
        style_block=style["prompt_block"],
        species_traits=traits,
    )


def iter_prompts(
    species_slug: str | None = None,
    style_slug: str | None = None,
    only_enabled: bool = True,
) -> Iterator[tuple[dict, dict, str]]:
    """Yield (species, style, prompt_text) tuples for each matching combo.

    Args:
        species_slug: Optional species slug filter.
        style_slug: Optional style slug filter.
        only_enabled: If True (default), skip records where enabled is False.

    Yields:
        Tuples of (species_record, style_record, rendered_prompt).
    """
    species_list = load_species()
    style_list = load_styles()

    for sp in species_list:
        if only_enabled and not sp.get("enabled", True):
            continue
        if species_slug is not None and sp.get("slug") != species_slug:
            continue
        for st in style_list:
            if only_enabled and not st.get("enabled", True):
                continue
            if style_slug is not None and st.get("slug") != style_slug:
                continue
            yield sp, st, build_prompt(sp, st)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate prompts from species x style combinations."
    )
    parser.add_argument("--species", default=None, help="Filter to one species slug.")
    parser.add_argument("--style", default=None, help="Filter to one style slug.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: print rendered prompts for matching combos."""
    args = _parse_args(argv)
    for sp, st, prompt in iter_prompts(
        species_slug=args.species, style_slug=args.style
    ):
        print(f"=== {sp['slug']} x {st['slug']} ===")
        print(prompt)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
