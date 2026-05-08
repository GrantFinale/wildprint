"""User-selectable poster style profiles.

A ``StyleProfile`` bundles every aesthetic decision that distinguishes one
named poster style from another: paper color, ink color, rule color, paper
grain intensity, inner-border treatment, title kind, label kind, and font
family. The renderer reads a profile and toggles its rendering knobs to
match.

Three profiles ship today:

``FIELD_GUIDE_PROFILE``
    The reference aesthetic from ``output/uploads/fish poster.jpeg``. Cream
    parchment paper, deep warm-brown ink, thin inner border, two-line title
    ("FISH OF" preheader + giant transitional-serif title), tracked
    common-name-only labels centered under each fish. The default.

``VINTAGE_TACKLE_PROFILE``
    Antique sporting-goods catalog. Warmer cream paper, dark sepia ink,
    gold rule + accent, double-diamond inner border, ornamental title
    frame with center-diamond glyph between rules, two-line label (bold
    common name + italic Latin scientific).

``LEGACY_PROFILE``
    Backwards-compatible white/black combo for any caller that doesn't
    request a profile. Preserves today's ``EditorialMultiRenderer``
    behavior exactly.

Add a new style by:
1. Defining a new ``StyleProfile`` constant in this module.
2. Mapping its slug in :func:`get_profile`.
3. (Optional) wiring a UI tile in ``review_app/templates/create.html``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TitleKind = Literal["classic", "two_line", "ornamental_frame"]
LabelKind = Literal["leader_lines", "tracked_common_only", "common_plus_latin_italic"]
InnerBorderKind = Literal["none", "thin", "double_diamond"]


@dataclass(frozen=True)
class StyleProfile:
    """Bundle of aesthetic knobs that define a named poster style.

    Attributes:
        name: User-facing label shown in the picker (e.g. "Field Guide").
        paper_hex: Background paper color, ``#RRGGBB``.
        ink_hex: Primary ink for title + labels.
        rule_hex: Color for horizontal rules and inner borders.
        accent_hex: Accent color (gold for Vintage Tackle's diamond glyphs;
            for profiles without an accent, fall back to ``rule_hex``).
        paper_grain_enabled: When True, the renderer tints the paper with
            a subtle noise texture so it reads as paper, not flat color.
        paper_grain_intensity: 0.0..1.0 — how strong the noise overlay is
            when ``paper_grain_enabled`` is True.
        inner_border: ``"none"`` (no border), ``"thin"`` (single rule),
            ``"double_diamond"`` (two rules with corner diamonds).
        title_kind: ``"classic"`` (legacy single-line + ornamental rule),
            ``"two_line"`` (preheader + giant title — the reference look),
            ``"ornamental_frame"`` (title flanked by rules with center-diamond
            glyphs above and below).
        label_kind: ``"leader_lines"`` (legacy thin connector lines to
            whitespace-positioned labels), ``"tracked_common_only"`` (small
            tracked uppercase common name centered below each fish),
            ``"common_plus_latin_italic"`` (bold common name on top, italic
            Latin name below — vintage catalog style).
        display_font_family: Title font family name (must match a TTF in
            ``assets/fonts/`` as ``<NoSpaces>-Regular.ttf`` or fall back).
        body_font_family: Body/label font family name, same lookup rule.
    """

    name: str
    paper_hex: str
    ink_hex: str
    rule_hex: str
    accent_hex: str
    paper_grain_enabled: bool
    paper_grain_intensity: float
    inner_border: InnerBorderKind
    title_kind: TitleKind
    label_kind: LabelKind
    display_font_family: str
    body_font_family: str


# --- Built-in profiles -------------------------------------------------------


FIELD_GUIDE_PROFILE = StyleProfile(
    name="Field Guide",
    paper_hex="#FAF6EA",
    ink_hex="#2A2A2D",
    rule_hex="#3A3A3D",
    accent_hex="#3A3A3D",
    paper_grain_enabled=True,
    paper_grain_intensity=0.10,
    inner_border="thin",
    title_kind="two_line",
    label_kind="tracked_common_only",
    display_font_family="Cormorant Garamond",
    body_font_family="Inter",
)


VINTAGE_TACKLE_PROFILE = StyleProfile(
    name="Vintage Tackle Catalog",
    paper_hex="#F4ECD8",
    ink_hex="#4A2C1F",
    rule_hex="#B8860B",
    accent_hex="#B8860B",
    paper_grain_enabled=True,
    paper_grain_intensity=0.18,
    inner_border="double_diamond",
    title_kind="ornamental_frame",
    label_kind="common_plus_latin_italic",
    display_font_family="Cormorant Garamond",
    body_font_family="Cormorant Garamond Italic",
)


LEGACY_PROFILE = StyleProfile(
    name="Classic",
    paper_hex="#FFFFFF",
    ink_hex="#1A1A1A",
    rule_hex="#1A1A1A",
    accent_hex="#1A1A1A",
    paper_grain_enabled=False,
    paper_grain_intensity=0.0,
    inner_border="none",
    title_kind="classic",
    label_kind="leader_lines",
    display_font_family="Cormorant Garamond",
    body_font_family="Inter",
)


def get_profile(layout_style: str) -> StyleProfile:
    """Map a ``layout_style`` slug to its ``StyleProfile``.

    Unknown slugs fall back to :data:`LEGACY_PROFILE` so legacy callers
    never crash on a typo.
    """
    s = (layout_style or "").strip().lower()
    if s == "field_guide":
        return FIELD_GUIDE_PROFILE
    if s == "vintage_tackle":
        return VINTAGE_TACKLE_PROFILE
    return LEGACY_PROFILE
