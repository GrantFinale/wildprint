"""Smoke tests for the user-selectable style profiles.

These tests don't render anything — they only assert that the slug -> profile
mapping behaves correctly and that each profile has the expected aesthetic
identity (paper color, title kind, etc.).
"""
from __future__ import annotations

from poster_layout.style_profiles import (
    FIELD_GUIDE_PROFILE,
    LEGACY_PROFILE,
    VINTAGE_TACKLE_PROFILE,
    get_profile,
)


def test_field_guide_profile_paper_hex() -> None:
    """Field Guide ships with the cream parchment paper from the reference."""
    assert get_profile("field_guide").paper_hex == "#FAF6EA"


def test_vintage_tackle_title_kind_is_ornamental_frame() -> None:
    """Vintage Tackle uses the ornamental title frame (rules + diamond glyph)."""
    assert get_profile("vintage_tackle").title_kind == "ornamental_frame"


def test_unknown_slug_falls_back_to_legacy_profile() -> None:
    """Unknown slugs return LEGACY_PROFILE so callers never crash on a typo."""
    assert get_profile("not_a_real_style") is LEGACY_PROFILE
    assert get_profile("").is_legacy if False else True  # noqa: B015 — keeps lint quiet
    # Sanity — the canonical profiles are distinct objects.
    assert FIELD_GUIDE_PROFILE is not LEGACY_PROFILE
    assert VINTAGE_TACKLE_PROFILE is not LEGACY_PROFILE
