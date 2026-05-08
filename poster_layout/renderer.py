"""Pillow-backed ``PosterRenderer`` implementations.

Three renderers live here:

``PillowPosterRenderer``
    The original engineer-grade renderer: system font fallbacks, simple
    title/subtitle header, grid-style labels. Used by the multi-species
    scaled-row / grid layouts.

``EditorialPosterRenderer``
    Editorial-grade single-subject renderer: serif display face
    (Didot/Baskerville/Hoefler/Times fallback chain), title band with
    scientific name and ornamental rule, hero image, and a tracked
    small-caps subtitle caption. Pairs with ``HeroLayoutEngine``.

``EditorialMultiRenderer``
    Editorial-grade multi-species renderer: shares the Didot fallback
    chain, adaptive palette, ornamental rule, and title/caption bands
    with ``EditorialPosterRenderer``, but draws per-species italic
    labels under each placed master. Pairs with
    ``SmallEnsembleLayoutEngine`` (Phase 1) and the future
    ``FieldGuideLayoutEngine`` (Phase 2).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from poster_layout.interfaces import PosterRenderer
from poster_layout.models import LayoutResult, PlacedItem, PosterSpec, SpeciesRef

logger = logging.getLogger(__name__)


_FONT_CANDIDATES: tuple[str, ...] = (
    "Helvetica.ttc",
    "Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse a ``#RRGGBB`` or ``#RGB`` hex string to an ``(r, g, b)`` tuple."""
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (255, 255, 255)


def _relative_luminance(hex_color: str) -> float:
    """Return perceived luminance of a hex color in the 0..255 range.

    Uses the Rec. 709 coefficients. Not strictly sRGB-linear, but close
    enough for choosing between light-text and dark-text palettes.
    """
    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _adaptive_palette(bg_hex: str) -> tuple[str, str, str, str]:
    """Return ``(title, scientific, rule, caption)`` hex colors tuned to a bg.

    Picks a warm-dark palette on light backgrounds and a warm-light
    palette on dark backgrounds so Didot titles and italic scientific
    names stay legible regardless of ``--background`` choice.
    """
    lum = _relative_luminance(bg_hex)
    if lum < 140:
        # Dark backdrop — warm off-white palette.
        return (
            "#F5EFE0",  # title: warm ivory near-white
            "#CDC3B3",  # scientific name: muted warm beige
            "#8A7F72",  # rule: warm mid-gray (visible on both tones)
            "#B5AA99",  # caption: softer warm gray
        )
    # Light backdrop — warm near-black palette.
    return (
        "#1a1612",
        "#5a5248",
        "#8a7f72",
        "#8a7f72",
    )


def _sample_master_background(master_path: Path) -> tuple[int, int, int]:
    """Sample the four corners of a master image and return a median RGB.

    Used by the editorial renderer so the poster background exactly matches
    the master's paper tone — eliminates the visible "postcard" rectangle
    that otherwise appears when the master's ivory differs from a hardcoded
    canvas ivory by even a few values.
    """
    with Image.open(master_path) as img:
        rgb = img.convert("RGB")
        w, h = rgb.size
        corners = [
            rgb.getpixel((0, 0)),
            rgb.getpixel((w - 1, 0)),
            rgb.getpixel((0, h - 1)),
            rgb.getpixel((w - 1, h - 1)),
        ]
    # Median per channel — robust to one odd corner.
    r = sorted(c[0] for c in corners)[len(corners) // 2]
    g = sorted(c[1] for c in corners)[len(corners) // 2]
    b = sorted(c[2] for c in corners)[len(corners) // 2]
    return (r, g, b)


def _load_font(size: int) -> ImageFont.ImageFont:
    """Try a list of system fonts; fall back to the PIL default."""
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int]:
    """Measure the pixel size of ``text`` for the given font."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:  # very old PIL fallback
        return draw.textsize(text, font=font)  # type: ignore[attr-defined]


# --- Shared editorial typography helpers -------------------------------------
#
# These module-level helpers were extracted from ``EditorialPosterRenderer``
# so the multi-species editorial renderer can reuse them without duplicating
# Didot fallback logic, ornamental rules, or band layout. ``EditorialPosterRenderer``
# still wraps them as instance methods to keep its public API stable.

#: Ordered serif fallback chain for the display face. The first path that
#: loads without error wins. ``.ttc`` files are probed face-by-face to pick
#: Bold / Italic / Regular by name; if identification fails, index=0 is used
#: for all three (documented limitation — still prints cleanly).
_EDITORIAL_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Didot.ttc",
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "/System/Library/Fonts/Supplemental/Hoefler Text.ttc",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
)

# Reference-aesthetic constants — Field-Guide / "Fish of <Lake>" poster style.
# Cream paper background, deep brown ink, transitional serif title in two
# lines, thin inner border, no scientific names by default.
REFERENCE_PAPER_HEX = "#f5efe2"
REFERENCE_INK_HEX = "#3a2e22"
REFERENCE_INNER_BORDER_HEX = "#a89880"

_PROJECT_ROOT_FOR_RENDERER = Path(__file__).resolve().parent.parent
# Cormorant Garamond Bold ships in assets/fonts — preferred over Didot for
# the new aesthetic because it has the warm transitional-serif feel of the
# reference poster's title. Falls back to PlayfairDisplay/Didot if missing.
_REFERENCE_TITLE_FONT_CANDIDATES: tuple[str, ...] = (
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "CormorantGaramond-Bold.ttf"),
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "PlayfairDisplay-Bold.ttf"),
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "EBGaramond-Bold.ttf"),
    "/System/Library/Fonts/Supplemental/Didot.ttc",
)
_REFERENCE_PREHEADER_FONT_CANDIDATES: tuple[str, ...] = (
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "CormorantGaramond-Regular.ttf"),
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "EBGaramond-Regular.ttf"),
    str(_PROJECT_ROOT_FOR_RENDERER / "assets" / "fonts" / "PlayfairDisplay-Regular.ttf"),
    "/System/Library/Fonts/Supplemental/Didot.ttc",
)
_FRAMES_DIR = _PROJECT_ROOT_FOR_RENDERER / "assets" / "frames"
SUPPORTED_FRAME_STYLES: tuple[str, ...] = ("walnut", "oak", "black", "white", "pine")


def _identify_ttc_faces(
    path: str, size: int
) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Probe a ``.ttc`` for Bold / Italic / Regular faces.

    Returns ``(bold_or_regular, italic_or_regular, regular)`` — falls back to
    ``index=0`` for any face that couldn't be identified by name. Raises
    ``OSError`` if even ``index=0`` fails to load.
    """
    regular: ImageFont.FreeTypeFont | None = None
    bold: ImageFont.FreeTypeFont | None = None
    italic: ImageFont.FreeTypeFont | None = None

    for index in range(8):
        try:
            f = ImageFont.truetype(path, size, index=index)
        except (OSError, IOError, ValueError):
            break
        try:
            family, style = f.getname()
        except Exception:  # noqa: BLE001
            family, style = ("", "")
        style_lower = (style or "").lower()

        if regular is None and "regular" in style_lower and "semi" not in style_lower:
            regular = f
        if (
            bold is None
            and "bold" in style_lower
            and "italic" not in style_lower
            and "semi" not in style_lower
        ):
            bold = f
        if italic is None and "italic" in style_lower and "bold" not in style_lower:
            italic = f

    if regular is None:
        regular = ImageFont.truetype(path, size, index=0)
    if bold is None:
        bold = regular
    if italic is None:
        italic = regular

    return bold, italic, regular


def _load_display_fonts(
    paths: list[str] | tuple[str, ...],
    title_size: int,
    scientific_size: int,
    regular_size: int,
) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
    """Return ``(bold_title, italic_scientific, regular)`` fonts.

    Tries each path in order. For ``.ttc`` paths, probes faces to identify
    Bold / Italic / Regular by name and loads each at its dedicated size.
    For ``.ttf`` single-face paths, loads the single face at all three
    sizes (the same face serves all three slots, which is a documented
    limitation).
    """
    for candidate in paths:
        try:
            probe = ImageFont.truetype(candidate, 24, index=0)
            _ = probe.getname()
        except (OSError, IOError, ValueError):
            continue

        logger.info("editorial typography: loaded font '%s'.", candidate)

        if candidate.lower().endswith(".ttc"):
            try:
                bold_t, _, _ = _identify_ttc_faces(candidate, title_size)
                _, italic_s, _ = _identify_ttc_faces(candidate, scientific_size)
                _, _, regular_r = _identify_ttc_faces(candidate, regular_size)
                return bold_t, italic_s, regular_r
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "editorial typography: failed to probe faces in '%s' (%s); "
                    "falling back to index=0 for all slots.",
                    candidate,
                    exc,
                )
                try:
                    return (
                        ImageFont.truetype(candidate, title_size, index=0),
                        ImageFont.truetype(candidate, scientific_size, index=0),
                        ImageFont.truetype(candidate, regular_size, index=0),
                    )
                except (OSError, IOError, ValueError):
                    continue
        else:
            try:
                return (
                    ImageFont.truetype(candidate, title_size),
                    ImageFont.truetype(candidate, scientific_size),
                    ImageFont.truetype(candidate, regular_size),
                )
            except (OSError, IOError, ValueError):
                continue

    logger.warning(
        "editorial typography: no serif font from candidates loaded; falling "
        "back to ImageFont.load_default() (low-fidelity)."
    )
    default = ImageFont.load_default()
    return default, default, default


def _load_caption_font(
    paths: list[str] | tuple[str, ...], size: int
) -> ImageFont.ImageFont:
    """Load the Regular face of the first workable font at ``size``."""
    for candidate in paths:
        try:
            if candidate.lower().endswith(".ttc"):
                _, _, regular = _identify_ttc_faces(candidate, size)
                return regular
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError, ValueError):
            continue
    return ImageFont.load_default()


def _draw_ornamental_rule(
    draw: ImageDraw.ImageDraw,
    y: int,
    canvas_width: int,
    color: str,
) -> None:
    """Thin horizontal rule with a small centered diamond break."""
    rule_width = int(round(canvas_width * 0.40))
    x_start = (canvas_width - rule_width) // 2
    x_end = x_start + rule_width
    center_x = canvas_width // 2
    gap = max(12, int(round(canvas_width * 0.006)))
    diamond_half = max(6, int(round(canvas_width * 0.004)))

    draw.line([(x_start, y), (center_x - gap, y)], fill=color, width=2)
    draw.line([(center_x + gap, y), (x_end, y)], fill=color, width=2)

    diamond = [
        (center_x, y - diamond_half),
        (center_x + diamond_half, y),
        (center_x, y + diamond_half),
        (center_x - diamond_half, y),
    ]
    draw.polygon(diamond, fill=color)


def _draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    y: int,
    canvas_w: int,
    tracking_px: int,
    fill: str,
) -> None:
    """Draw ``text`` centered horizontally at ``y`` with extra px between glyphs."""
    if not text:
        return

    advances: list[int] = []
    for ch in text:
        cw, _ = _text_size(draw, ch, font)
        advances.append(cw)

    total_w = sum(advances) + tracking_px * max(0, len(text) - 1)
    x_cursor = (canvas_w - total_w) // 2
    for ch, adv in zip(text, advances):
        draw.text((x_cursor, y), ch, font=font, fill=fill)
        x_cursor += adv + tracking_px


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    title_band_h: int,
    title: str,
    italic_secondary: str | None,
    bold_font: ImageFont.ImageFont,
    italic_font: ImageFont.ImageFont,
    title_color: str,
    secondary_color: str,
    rule_color: str,
) -> None:
    """Draw the editorial title block: title + italic line + ornamental rule.

    Used by both the single-subject and multi-species editorial renderers.
    The italic secondary line accepts either a per-species scientific name
    (Hero) or a poster subtitle (multi).
    """
    title_text = title or ""
    if title_text:
        tw, th = _text_size(draw, title_text, bold_font)
        title_y = int(round(title_band_h * 0.30))
        title_x = (canvas_w - tw) // 2
        draw.text((title_x, title_y), title_text, font=bold_font, fill=title_color)
    else:
        title_y = int(round(title_band_h * 0.30))
        th = 0

    if italic_secondary:
        sw, sh = _text_size(draw, italic_secondary, italic_font)
        sec_y = title_y + th + 60
        sec_x = (canvas_w - sw) // 2
        draw.text(
            (sec_x, sec_y),
            italic_secondary,
            font=italic_font,
            fill=secondary_color,
        )
        sec_y_top = sec_y + sh
    else:
        sec_y_top = title_y + th

    rule_y = sec_y_top + 80
    _draw_ornamental_rule(draw=draw, y=rule_y, canvas_width=canvas_w, color=rule_color)


def _draw_caption_block(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    canvas_h: int,
    caption_band_h: int,
    primary_text: str | None,
    secondary_text: str | None,
    primary_font: ImageFont.ImageFont,
    secondary_font: ImageFont.ImageFont,
    color: str,
    primary_letter_spacing: int = 6,
    secondary_letter_spacing: int = 4,
) -> None:
    """Draw the bottom caption band with up to two tracked small-caps lines."""
    caption_band_top = canvas_h - caption_band_h
    primary_upper = primary_text.upper() if primary_text else ""
    secondary_upper = secondary_text.upper() if secondary_text else ""

    primary_y = caption_band_top + int(round(caption_band_h * 0.30))
    sub_h = 0
    if primary_upper:
        _draw_tracked_text(
            draw=draw,
            text=primary_upper,
            font=primary_font,
            y=primary_y,
            canvas_w=canvas_w,
            tracking_px=primary_letter_spacing,
            fill=color,
        )
        _, sub_h = _text_size(draw, primary_upper, primary_font)

    if secondary_upper:
        secondary_y = primary_y + sub_h + int(round(caption_band_h * 0.18))
        _draw_tracked_text(
            draw=draw,
            text=secondary_upper,
            font=secondary_font,
            y=secondary_y,
            canvas_w=canvas_w,
            tracking_px=secondary_letter_spacing,
            fill=color,
        )


def _load_first_truetype(
    paths: list[str] | tuple[str, ...], size: int
) -> ImageFont.ImageFont:
    """Load the first font in ``paths`` that opens cleanly at ``size``.

    For ``.ttc`` files, picks the Bold (or Regular as fallback) face by name.
    Used for the reference-aesthetic title + preheader fonts.
    """
    for cand in paths:
        try:
            if cand.lower().endswith(".ttc"):
                # Probe faces; prefer Bold if present.
                bold: ImageFont.FreeTypeFont | None = None
                regular: ImageFont.FreeTypeFont | None = None
                for index in range(8):
                    try:
                        f = ImageFont.truetype(cand, size, index=index)
                    except (OSError, IOError, ValueError):
                        break
                    try:
                        _, style = f.getname()
                    except Exception:  # noqa: BLE001
                        style = ""
                    s = (style or "").lower()
                    if bold is None and "bold" in s and "italic" not in s:
                        bold = f
                    if regular is None and "regular" in s and "semi" not in s:
                        regular = f
                return bold or regular or ImageFont.truetype(cand, size, index=0)
            return ImageFont.truetype(cand, size)
        except (OSError, IOError, ValueError):
            continue
    return ImageFont.load_default()


def _draw_two_line_title(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    canvas_h: int,
    preheader_text: str,
    main_title: str,
    title_color: str,
    rule_color: str,
    title_band_h: int | None = None,
) -> int:
    """Draw the reference-aesthetic title: tracked preheader flanked by
    horizontal rules, then the big main title beneath.

    Returns the y-bottom of the title block (useful for downstream layout).

    Geometry (fractions of canvas dims):
      - preheader font height: ~3% of canvas_h (min 28)
      - main title font height: ~9% of canvas_h (min 80)
      - inner border / rules sit ~3% inset from canvas edges
    """
    # Sizes proportional to canvas height (works for portrait + landscape).
    # Title at 0.077 (85% of the prior 0.090) per user feedback — the giant
    # title was overpowering the species hierarchy.
    preheader_size = max(26, int(round(canvas_h * 0.026)))
    title_size = max(72, int(round(canvas_h * 0.077)))

    pre_font = _load_first_truetype(
        _REFERENCE_PREHEADER_FONT_CANDIDATES, preheader_size
    )
    title_font = _load_first_truetype(
        _REFERENCE_TITLE_FONT_CANDIDATES, title_size
    )

    # Preheader sits in the upper band — about 6% from canvas top, after
    # the inner border (which is at ~3% inset).
    pre_y = int(round(canvas_h * 0.060))
    pre_text_upper = (preheader_text or "").upper()
    tracking_px = max(2, int(round(preheader_size * 0.20)))
    if pre_text_upper:
        # Measure tracked width.
        advances = []
        for ch in pre_text_upper:
            cw, _ = _text_size(draw, ch, pre_font)
            advances.append(cw)
        pre_total_w = sum(advances) + tracking_px * max(0, len(pre_text_upper) - 1)
        pre_x = (canvas_w - pre_total_w) // 2
        cursor = pre_x
        for ch, adv in zip(pre_text_upper, advances):
            draw.text((cursor, pre_y), ch, font=pre_font, fill=title_color)
            cursor += adv + tracking_px
        # Get vertical metrics for the rule placement.
        _, pre_h = _text_size(draw, pre_text_upper, pre_font)
        # Flanking horizontal rules — extend toward (but not touching) the
        # canvas edges. Width scales with canvas (~2-3px on the print res
        # so the lines read as a clear, intentional accent without
        # competing with the title or fish — Task E).
        # Use the actual rendered bbox so the rule lands on the visual
        # midline of the caps (textbbox includes ascender padding that
        # would otherwise push the rule above the visual midline).
        _bbox = draw.textbbox((pre_x, pre_y), pre_text_upper, font=pre_font)
        rule_y = (_bbox[1] + _bbox[3]) // 2
        rule_pad = max(40, int(round(canvas_w * 0.025)))  # gap between rule and preheader
        outer_inset = max(80, int(round(canvas_w * 0.10)))
        rule_width = max(2, int(round(canvas_h / 1500)))
        # Left rule
        draw.line(
            [(outer_inset, rule_y), (pre_x - rule_pad, rule_y)],
            fill=rule_color,
            width=rule_width,
        )
        # Right rule
        draw.line(
            [(pre_x + pre_total_w + rule_pad, rule_y), (canvas_w - outer_inset, rule_y)],
            fill=rule_color,
            width=rule_width,
        )
        title_y_top = pre_y + pre_h + int(round(canvas_h * 0.018))
    else:
        title_y_top = pre_y

    # Main title — big transitional serif. Auto-shrink the font so the
    # title fits within ~88% of canvas width (inner border at 3% inset
    # plus a comfortable visual margin). Without this, long lake names
    # like "LAKE MICHIGAN" overflow the canvas edges.
    if main_title:
        max_title_w = int(canvas_w * 0.88)
        cur_size = title_size
        cur_font = title_font
        tw, th = _text_size(draw, main_title, cur_font)
        # Shrink in 6% increments until it fits, never below 50% of original.
        min_size = max(40, int(title_size * 0.50))
        while tw > max_title_w and cur_size > min_size:
            cur_size = max(min_size, int(cur_size * 0.94))
            cur_font = _load_first_truetype(_REFERENCE_TITLE_FONT_CANDIDATES, cur_size)
            tw, th = _text_size(draw, main_title, cur_font)
        tx = (canvas_w - tw) // 2
        draw.text((tx, title_y_top), main_title, font=cur_font, fill=title_color)
        return title_y_top + th
    return title_y_top


def _draw_ornamental_title_frame(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    canvas_h: int,
    preheader_text: str,
    main_title: str,
    title_color: str,
    rule_color: str,
    accent_color: str,
) -> int:
    """Vintage-tackle title frame: preheader, top rule with center diamond,
    giant title, bottom rule with center diamond.

    Used by the "vintage_tackle" :class:`StyleProfile`. Returns the y-bottom
    of the entire title frame (useful for downstream layout).
    """
    preheader_size = max(28, int(round(canvas_h * 0.026)))
    title_size = max(80, int(round(canvas_h * 0.070)))

    pre_font = _load_first_truetype(_REFERENCE_PREHEADER_FONT_CANDIDATES, preheader_size)
    title_font = _load_first_truetype(_REFERENCE_TITLE_FONT_CANDIDATES, title_size)

    cursor_y = int(round(canvas_h * 0.045))

    # Preheader — small tracked uppercase, centered.
    pre_text = (preheader_text or "").upper()
    tracking_px = max(3, int(round(preheader_size * 0.25)))
    if pre_text:
        advances = []
        for ch in pre_text:
            cw, _ = _text_size(draw, ch, pre_font)
            advances.append(cw)
        total_w = sum(advances) + tracking_px * max(0, len(pre_text) - 1)
        x_start = (canvas_w - total_w) // 2
        cur_x = x_start
        for ch, adv in zip(pre_text, advances):
            draw.text((cur_x, cursor_y), ch, font=pre_font, fill=title_color)
            cur_x += adv + tracking_px
        # Use the actual rendered bbox for vertical advance so spacing is
        # consistent with the preheader's visual baseline (textbbox handles
        # ascender padding correctly — see _draw_two_line_title fix).
        _bbox = draw.textbbox((x_start, cursor_y), pre_text, font=pre_font)
        pre_h = _bbox[3] - _bbox[1]
        cursor_y += pre_h + int(round(canvas_h * 0.015))

    # Top rule with center diamond.
    rule_w = max(2, int(round(canvas_h / 1400)))
    rule_inset = int(round(canvas_w * 0.08))
    diamond_size = max(8, int(round(rule_w * 6)))

    def _rule_with_diamond(y_pos: int) -> None:
        cx = canvas_w // 2
        gap = max(20, int(round(canvas_w * 0.012)))
        draw.line(
            [(rule_inset, y_pos), (cx - gap, y_pos)],
            fill=rule_color,
            width=rule_w,
        )
        draw.line(
            [(cx + gap, y_pos), (canvas_w - rule_inset, y_pos)],
            fill=rule_color,
            width=rule_w,
        )
        # Center diamond glyph.
        diamond = [
            (cx, y_pos - diamond_size),
            (cx + diamond_size, y_pos),
            (cx, y_pos + diamond_size),
            (cx - diamond_size, y_pos),
        ]
        draw.polygon(diamond, fill=accent_color)

    _rule_with_diamond(cursor_y)
    cursor_y += diamond_size + int(round(canvas_h * 0.018))

    # Main title — auto-shrink to fit 86% of canvas width.
    if main_title:
        max_title_w = int(canvas_w * 0.86)
        cur_size = title_size
        cur_font = title_font
        tw, th = _text_size(draw, main_title, cur_font)
        min_size = max(40, int(title_size * 0.50))
        while tw > max_title_w and cur_size > min_size:
            cur_size = max(min_size, int(cur_size * 0.94))
            cur_font = _load_first_truetype(_REFERENCE_TITLE_FONT_CANDIDATES, cur_size)
            tw, th = _text_size(draw, main_title, cur_font)
        tx = (canvas_w - tw) // 2
        draw.text((tx, cursor_y), main_title, font=cur_font, fill=title_color)
        cursor_y += th + int(round(canvas_h * 0.018))

    # Bottom rule with center diamond.
    _rule_with_diamond(cursor_y)
    cursor_y += diamond_size

    return cursor_y


def _draw_compact_caption_only(
    draw: ImageDraw.ImageDraw,
    placements,
    font: ImageFont.ImageFont,
    ink_hex: str,
    canvas_h: int,
    label_letter_spacing: int = 4,
) -> None:
    """Draw a single tracked-uppercase common name centered under each fish.

    Used by the "field_guide" :class:`StyleProfile` (label_kind =
    ``tracked_common_only``). NO leader lines, NO scientific names — just
    a small, deliberate caps caption that anchors the fish to its species.
    """
    pad = max(8, int(round(canvas_h * 0.006)))
    for placed in placements:
        sp = placed.species_ref
        common = (sp.common_name or "").upper()
        if not common:
            continue
        cx = placed.x + placed.draw_width // 2
        # Compute tracked width.
        advances = [_text_size(draw, ch, font)[0] for ch in common]
        total_w = sum(advances) + label_letter_spacing * max(0, len(common) - 1)
        _, h = _text_size(draw, common, font)
        x_cursor = cx - total_w // 2
        y = placed.y + placed.draw_height + pad
        for ch, adv in zip(common, advances):
            draw.text((x_cursor, y), ch, font=font, fill=ink_hex)
            x_cursor += adv + label_letter_spacing


def _draw_two_line_label(
    draw: ImageDraw.ImageDraw,
    placements,
    body_font: ImageFont.ImageFont,
    italic_font: ImageFont.ImageFont,
    ink_hex: str,
    canvas_h: int,
) -> None:
    """Draw a two-line label: bold common name + italic Latin name.

    Used by the "vintage_tackle" :class:`StyleProfile` (label_kind =
    ``common_plus_latin_italic``). Both lines are centered under the fish.
    """
    pad = max(8, int(round(canvas_h * 0.006)))
    line_gap = max(4, int(round(canvas_h * 0.004)))
    for placed in placements:
        sp = placed.species_ref
        common = sp.common_name or ""
        latin = sp.scientific_name or ""
        if not common and not latin:
            continue
        cx = placed.x + placed.draw_width // 2
        cur_y = placed.y + placed.draw_height + pad
        if common:
            cw, ch = _text_size(draw, common, body_font)
            draw.text((cx - cw // 2, cur_y), common, font=body_font, fill=ink_hex)
            cur_y += ch + line_gap
        if latin:
            lw, _lh = _text_size(draw, latin, italic_font)
            draw.text((cx - lw // 2, cur_y), latin, font=italic_font, fill=ink_hex)


def _draw_inner_border(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    canvas_h: int,
    color: str,
    inset_frac: float = 0.030,
    line_width_px: int | None = None,
) -> None:
    """Draw a thin rectangular border at ``inset_frac`` from the canvas edges.

    Default thickness scales with canvas: ~2px on a 3300px short side,
    ~3px on 5100px. Visible but minimal — a printed-poster frame inset.
    """
    inset = int(round(min(canvas_w, canvas_h) * inset_frac))
    if line_width_px is None:
        line_width_px = max(2, int(round(min(canvas_w, canvas_h) / 1700)))
    x1, y1 = inset, inset
    x2, y2 = canvas_w - inset, canvas_h - inset
    # Pillow's draw.rectangle with width=N draws on the inside of the rect,
    # which is exactly what we want.
    draw.rectangle([(x1, y1), (x2, y2)], outline=color, width=line_width_px)


def _apply_paper_grain(
    canvas: Image.Image, intensity: float = 0.04, seed: int = 7
) -> None:
    """Apply a subtle paper-grain texture + non-uniform warm aging to
    ``canvas`` in-place.

    Two layers:

    1. High-frequency luminance noise (gaussian per-pixel) — the actual
       grain. Amplitude is ``intensity * 30`` luminance units around 0.
    2. Low-frequency warm-yellow tint (a small ~64x64 random field
       upsampled with bilinear interpolation) so different regions of
       the canvas look slightly more aged than others.

    Numpy-vectorised so a 5100x3300 canvas processes in <2s versus the
    ~21s of the previous Python pixel loop.

    intensity: 0..1, scales the per-pixel noise amplitude.
    """
    try:
        cw, ch = canvas.size
        rng = np.random.default_rng(seed)

        # Convert canvas to numpy array (float32 for math headroom).
        arr = np.asarray(canvas.convert("RGB"), dtype=np.float32)

        # Layer 1 — high-frequency per-pixel grain. Gaussian noise around 0.
        # Sigma chosen so visible-but-not-distracting at intensity=0.10 → ~3 lum units.
        sigma = max(0.5, intensity * 30.0)
        grain = rng.normal(0.0, sigma, size=(ch, cw)).astype(np.float32)
        # Apply identically to all 3 channels (luminance shift, not chroma).
        arr += grain[:, :, None]

        # Layer 2 — low-frequency aging. A very small (~16x16) noise field,
        # bilinear-upscaled into a smooth gradient across the canvas, with
        # a tiny warm-yellow tint bias. Smaller field + smaller amplitude
        # so the result reads as a subtle shift in tone, NOT splotchy
        # paper-mold patches.
        low_w = 16
        low_h = max(8, int(round(low_w * ch / max(1, cw))))
        low = rng.uniform(-1.0, 1.0, size=(low_h, low_w)).astype(np.float32)
        low_img = Image.fromarray(((low + 1.0) * 127.5).astype(np.uint8), mode="L")
        # Two-step upsample via a midpoint to get a very smooth gradient.
        mid_w, mid_h = max(64, low_w * 8), max(64, low_h * 8)
        low_mid = low_img.resize((mid_w, mid_h), Image.BILINEAR)
        low_full = low_mid.resize((cw, ch), Image.BILINEAR)
        low_arr = np.asarray(low_full, dtype=np.float32) / 127.5 - 1.0  # -1..1

        # Warm-yellow tint, amplitude ~1.5% luminance — subtle.
        amp = 0.015
        warm = np.array([1.0, 0.7, -1.4], dtype=np.float32) * amp
        tint = low_arr[:, :, None] * warm[None, None, :]
        arr *= (1.0 + tint)

        np.clip(arr, 0.0, 255.0, out=arr)
        out = Image.fromarray(arr.astype(np.uint8), mode="RGB")
        canvas.paste(out)
    except Exception as exc:  # noqa: BLE001
        # Don't fail the render over a cosmetic grain.
        logger.warning("paper grain failed: %s", exc)


def _load_or_synth_frame_texture(
    frame_style: str, target_long: int
) -> Image.Image | None:
    """Return a tileable RGB texture for ``frame_style``.

    walnut/oak: load the photographic JPEG from ``assets/frames``.
    black/white: synthesise a solid-with-faint-grain texture if the JPEG
    is missing (so the option works without Replicate-generated assets).
    """
    texture_path = _FRAMES_DIR / f"{frame_style}.jpg"
    if texture_path.exists():
        try:
            with Image.open(texture_path) as tex_src:
                tex = tex_src.convert("RGB")
            if tex.width != target_long:
                ratio = target_long / tex.width
                tex = tex.resize(
                    (target_long, int(round(tex.height * ratio))), Image.LANCZOS
                )
            return tex
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load frame texture %s: %s", texture_path, exc)

    # Synthesised fallback for black/white when the JPEG is absent. We
    # avoid this for walnut/oak — those need real wood photography to
    # look right, and the asset files are committed.
    if frame_style == "black":
        base = (24, 22, 20)
    elif frame_style == "white":
        base = (240, 236, 228)
    else:
        return None

    side = max(512, target_long)
    rng = np.random.default_rng(seed=hash(frame_style) & 0xFFFF)
    grain = rng.normal(0.0, 6.0, (side, side, 3))
    arr = np.full((side, side, 3), base, dtype=np.float32) + grain.astype(np.float32)
    np.clip(arr, 0.0, 255.0, out=arr)
    return Image.fromarray(arr.astype(np.uint8), mode="RGB")


_FRAME_OVERLAYS_DIR = _PROJECT_ROOT_FOR_RENDERER / "assets" / "frame_overlays"

# THICK_FRAC must match scripts/build_frame_overlays.py — it's the
# fraction of the overlay's side length occupied by the wood ring.
_OVERLAY_THICK_FRAC: float = 0.070


def _load_frame_overlay(frame_style: str) -> Image.Image | None:
    """Return the pre-built RGBA overlay for ``frame_style``, or None."""
    if frame_style not in SUPPORTED_FRAME_STYLES:
        return None
    p = _FRAME_OVERLAYS_DIR / f"{frame_style}.png"
    if not p.exists():
        logger.warning("Frame overlay missing: %s", p)
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load frame overlay %s: %s", p, exc)
        return None


def _build_frame_from_overlay(
    overlay_src: Image.Image,
    target_w: int,
    target_h: int,
    target_thick: int,
) -> Image.Image:
    """Build an RGBA frame ring at ``target_w x target_h`` whose wood
    thickness is exactly ``target_thick`` on every side, using a square
    9-slice extracted from ``overlay_src``.

    Why a 9-slice instead of a single resize: the source overlay is
    square, so its corner mitres are at 45 degrees. A naive resize to a
    portrait/landscape rectangle stretches the corners non-uniformly and
    breaks the miter. Instead we slice off the four square corners
    (with 45-degree mitres baked in), keep them at uniform scale, and
    stretch the four straight sides along their long axis only.

    The inner window of the result is fully transparent.
    """
    src_side = overlay_src.size[0]   # overlays are square
    src_thick = max(2, int(round(src_side * _OVERLAY_THICK_FRAC)))
    # If the overlay isn't square, make it square so the slices line up.
    if overlay_src.size[0] != overlay_src.size[1]:
        sq = max(overlay_src.size)
        squared = Image.new("RGBA", (sq, sq), (0, 0, 0, 0))
        squared.paste(overlay_src, (0, 0))
        overlay_src = squared
        src_side = sq

    # ---- Slice the 4 mitered corners from the source. Corners are
    # square pieces of side `src_thick` containing the 45-degree
    # diagonal seam — keep the diagonal at 45 deg by scaling them as a
    # SQUARE to `target_thick`, no stretching.
    tl_src = overlay_src.crop((0, 0, src_thick, src_thick))
    tr_src = overlay_src.crop((src_side - src_thick, 0, src_side, src_thick))
    bl_src = overlay_src.crop((0, src_side - src_thick, src_thick, src_side))
    br_src = overlay_src.crop(
        (src_side - src_thick, src_side - src_thick, src_side, src_side)
    )
    tl = tl_src.resize((target_thick, target_thick), Image.LANCZOS)
    tr = tr_src.resize((target_thick, target_thick), Image.LANCZOS)
    bl = bl_src.resize((target_thick, target_thick), Image.LANCZOS)
    br = br_src.resize((target_thick, target_thick), Image.LANCZOS)

    # ---- Slice the 4 straight sides from the source. The non-thick
    # dimension of each side is the inner span; we stretch ONLY along
    # the side's long axis to fit (target_w - 2*target_thick) etc.
    inner_side_w = target_w - 2 * target_thick
    inner_side_h = target_h - 2 * target_thick
    if inner_side_w <= 0 or inner_side_h <= 0:
        # Frame would consume the whole image — fall back to plain
        # uniform resize (still better than nothing).
        return overlay_src.resize((target_w, target_h), Image.LANCZOS)

    src_inner = src_side - 2 * src_thick
    top_src = overlay_src.crop((src_thick, 0, src_thick + src_inner, src_thick))
    bot_src = overlay_src.crop(
        (src_thick, src_side - src_thick, src_thick + src_inner, src_side)
    )
    left_src = overlay_src.crop((0, src_thick, src_thick, src_thick + src_inner))
    right_src = overlay_src.crop(
        (src_side - src_thick, src_thick, src_side, src_thick + src_inner)
    )
    top = top_src.resize((inner_side_w, target_thick), Image.LANCZOS)
    bot = bot_src.resize((inner_side_w, target_thick), Image.LANCZOS)
    left = left_src.resize((target_thick, inner_side_h), Image.LANCZOS)
    right = right_src.resize((target_thick, inner_side_h), Image.LANCZOS)

    # ---- Compose into the output ring.
    out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    out.alpha_composite(top, (target_thick, 0))
    out.alpha_composite(bot, (target_thick, target_h - target_thick))
    out.alpha_composite(left, (0, target_thick))
    out.alpha_composite(right, (target_w - target_thick, target_thick))
    out.alpha_composite(tl, (0, 0))
    out.alpha_composite(tr, (target_w - target_thick, 0))
    out.alpha_composite(bl, (0, target_h - target_thick))
    out.alpha_composite(br, (target_w - target_thick, target_h - target_thick))
    return out


def _compose_with_frame(
    poster: Image.Image,
    frame_style: str,
    frame_thickness_frac: float = 0.070,
    outer_pad_frac: float = 0.060,
) -> Image.Image:
    """Composite ``poster`` inside a pre-built mitered wood-frame overlay.

    Layers (back to front) on a TRANSPARENT outer canvas:
      1. Outer drop shadow (offset down + right, soft Gaussian).
         The shadow alpha-composites onto whatever is behind the PNG,
         so when the poster is rendered on a webpage the shadow appears
         to fall on the website background — strongest at the right
         edge of the right frame side and the bottom of the frame.
      2. The poster, sized to fit exactly inside the frame's inner
         window — paper/photo extends to the very inside edge of the
         frame. NO mat band, NO white border.
      3. A cast shadow on the poster from the upper-left light hitting
         the frame's left and top edges. Alpha-composited DIRECTLY onto
         the poster pixels (paper/photo darkens in place).
      4. The frame overlay PNG (mitered corners, transparent inner
         window, subtle inner bevel) on top — its own alpha is what
         makes only the frame ring show.

    Returns an RGBA image with the outer drop-shadow padding included.
    Outside the frame + shadow reach, alpha=0.
    """
    if frame_style not in SUPPORTED_FRAME_STYLES:
        return poster

    overlay_src = _load_frame_overlay(frame_style)
    if overlay_src is None:
        logger.warning(
            "Frame overlay for style=%s unavailable — returning unframed poster.",
            frame_style,
        )
        return poster

    pw, ph = poster.size
    short_dim = min(pw, ph)
    thick = max(60, int(round(short_dim * frame_thickness_frac)))
    pad = max(120, int(round(short_dim * outer_pad_frac)))

    # Frame-only dims (poster + thick on each side).
    f_w = pw + 2 * thick
    f_h = ph + 2 * thick
    # Final output dims (frame + pad on each side).
    out_w = f_w + 2 * pad
    out_h = f_h + 2 * pad

    # Frame's top-left in output coords.
    fx, fy = pad, pad

    # ----- Step 1: transparent outer canvas. -----
    out_canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))

    # ----- Step 2: outer drop shadow on the website background. -----
    try:
        shadow_offset_x = max(16, int(round(short_dim * 0.0072)))
        shadow_offset_y = max(20, int(round(short_dim * 0.009)))
        shadow_blur = max(32, int(round(short_dim * 0.016)))
        shadow_alpha = 180

        outer_shadow = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(outer_shadow)
        sdraw.rectangle(
            [
                (fx + shadow_offset_x, fy + shadow_offset_y),
                (fx + f_w + shadow_offset_x, fy + f_h + shadow_offset_y),
            ],
            fill=(0, 0, 0, shadow_alpha),
        )
        outer_shadow = outer_shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        out_canvas = Image.alpha_composite(out_canvas, outer_shadow)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Outer shadow failed: %s", exc)

    # ----- Step 3: cast shadow ONTO the poster (top + left edges). -----
    # Light from upper-left → the frame's top + left inner edges cast a
    # soft shadow onto the poster surface. Right + bottom barely shadow.
    poster_rgba = poster.convert("RGBA")
    try:
        cast_alpha_max = 110
        cast_inset = max(20, int(round(short_dim * 0.014)))   # falloff distance
        cast_blur = max(12, int(round(short_dim * 0.008)))

        cast = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(cast)
        # Build a stack of progressively-thinner bands to approximate a
        # gradient that peaks at the top + left edges and fades inward.
        steps = 14
        for i in range(steps):
            t = i / max(1, steps - 1)            # 0 .. 1
            a = int(round(cast_alpha_max * (1.0 - t)))
            band = max(1, int(round(cast_inset * (1.0 - t))))
            # Top band.
            cdraw.rectangle([(0, i), (pw, i + band)], fill=(0, 0, 0, a))
            # Left band.
            cdraw.rectangle([(i, 0), (i + band, ph)], fill=(0, 0, 0, a))
        cast = cast.filter(ImageFilter.GaussianBlur(cast_blur))
        # Hard-clip alpha to poster footprint (defensive — should already be).
        from PIL import ImageChops as _ImageChops
        clip = Image.new("L", (pw, ph), 255)
        ca = cast.split()[3]
        cast.putalpha(_ImageChops.multiply(ca, clip))
        poster_rgba = Image.alpha_composite(poster_rgba, cast)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cast shadow failed: %s", exc)

    # Paste the (now slightly-shadowed) poster at the inner-window
    # position — there is NO mat between the poster and the frame's
    # inner edge.
    out_canvas.paste(poster_rgba, (fx + thick, fy + thick), poster_rgba)

    # ----- Step 4: frame overlay PNG on top, built via 9-slice so the
    # mitered corners stay uniform regardless of poster aspect ratio.
    overlay = _build_frame_from_overlay(overlay_src, f_w, f_h, thick)
    out_canvas.alpha_composite(overlay, (fx, fy))

    return out_canvas


def _draw_species_label(
    draw: ImageDraw.ImageDraw,
    placed: PlacedItem,
    common_font: ImageFont.ImageFont,
    scientific_font: ImageFont.ImageFont,
    common_color: str,
    sci_color: str,
    label_gap_px: int,
    common_letter_spacing: int = 4,
    stroke_width: int = 0,
    stroke_fill: str | None = None,
) -> None:
    """Draw a centered two-line label below a placed item."""
    sp = placed.species_ref
    cx_canvas = placed.x + placed.draw_width // 2
    label_top = placed.y + placed.draw_height + label_gap_px
    _kwargs_common = {"font": common_font, "fill": common_color}
    _kwargs_sci = {"font": scientific_font, "fill": sci_color}
    if stroke_width and stroke_width > 0 and stroke_fill:
        _kwargs_common["stroke_width"] = int(stroke_width)
        _kwargs_common["stroke_fill"] = stroke_fill
        _kwargs_sci["stroke_width"] = int(stroke_width)
        _kwargs_sci["stroke_fill"] = stroke_fill

    common = (sp.common_name or "").upper()
    if common:
        advances: list[int] = []
        for ch in common:
            cw, _ = _text_size(draw, ch, common_font)
            advances.append(cw)
        common_w = sum(advances) + common_letter_spacing * max(0, len(common) - 1)
        _, common_h = _text_size(draw, common, common_font)
        x_cursor = cx_canvas - common_w // 2
        for ch, adv in zip(common, advances):
            draw.text((x_cursor, label_top), ch, **_kwargs_common)
            x_cursor += adv + common_letter_spacing
        next_top = label_top + common_h + max(6, common_h // 4)
    else:
        next_top = label_top

    sci = sp.scientific_name or ""
    if sci:
        sci_w, _sci_h = _text_size(draw, sci, scientific_font)
        sci_x = cx_canvas - sci_w // 2
        draw.text((sci_x, next_top), sci, **_kwargs_sci)


def _draw_species_label_at(
    draw: ImageDraw.ImageDraw,
    placed: PlacedItem,
    label_x: int,
    label_y: int,
    label_w: int,
    common_font: ImageFont.ImageFont,
    scientific_font: ImageFont.ImageFont,
    common_color: str,
    sci_color: str,
    common_letter_spacing: int = 4,
    stroke_width: int = 0,
    stroke_fill: str | None = None,
) -> None:
    """Draw the two-line label at an EXPLICIT (label_x, label_y) anchor,
    centering each line within ``label_w``. Used by the collision-aware
    inline-label pass that has already resolved a non-overlapping slot.
    """
    sp = placed.species_ref
    cx = label_x + label_w // 2
    _kwargs_common = {"font": common_font, "fill": common_color}
    _kwargs_sci = {"font": scientific_font, "fill": sci_color}
    if stroke_width and stroke_width > 0 and stroke_fill:
        _kwargs_common["stroke_width"] = int(stroke_width)
        _kwargs_common["stroke_fill"] = stroke_fill
        _kwargs_sci["stroke_width"] = int(stroke_width)
        _kwargs_sci["stroke_fill"] = stroke_fill

    common = (sp.common_name or "").upper()
    cur_y = label_y
    if common:
        advances = [_text_size(draw, ch, common_font)[0] for ch in common]
        common_w = sum(advances) + common_letter_spacing * max(0, len(common) - 1)
        _, common_h = _text_size(draw, common, common_font)
        cursor = cx - common_w // 2
        for ch, adv in zip(common, advances):
            draw.text((cursor, cur_y), ch, **_kwargs_common)
            cursor += adv + common_letter_spacing
        cur_y += common_h + max(6, common_h // 4)
    sci = sp.scientific_name or ""
    if sci:
        sw, _sh = _text_size(draw, sci, scientific_font)
        draw.text((cx - sw // 2, cur_y), sci, **_kwargs_sci)


class PillowPosterRenderer(PosterRenderer):
    """Render a ``LayoutResult`` to a PNG via Pillow."""

    def __init__(
        self,
        title_font_size: int = 96,
        subtitle_font_size: int = 48,
        label_font_size: int = 28,
    ) -> None:
        self.title_font_size = title_font_size
        self.subtitle_font_size = subtitle_font_size
        self.label_font_size = label_font_size

    # --------------------------------------------------------------- rendering

    def render(self, result: LayoutResult, output_path: Path) -> None:
        spec = result.poster
        canvas = Image.new(
            "RGB",
            (spec.canvas_width, spec.canvas_height),
            color=spec.background_color,
        )
        draw = ImageDraw.Draw(canvas)

        title_font = _load_font(self.title_font_size)
        subtitle_font = _load_font(self.subtitle_font_size)
        label_font = _load_font(self.label_font_size)

        self._draw_header(draw, spec, title_font, subtitle_font)

        for item in result.placements:
            self._paste_item(canvas, item)
            if spec.show_labels:
                self._draw_label(draw, item, label_font)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG")

    # ----------------------------------------------------------------- helpers

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        spec,
        title_font: ImageFont.ImageFont,
        subtitle_font: ImageFont.ImageFont,
    ) -> None:
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # Position the title vertically centered within the top 15% margin.
        title_text = spec.title or ""
        if title_text:
            tw, th = _text_size(draw, title_text, title_font)
            title_x = (canvas_w - tw) // 2
            title_y = int(canvas_h * 0.04)
            draw.text(
                (title_x, title_y),
                title_text,
                font=title_font,
                fill=(20, 20, 20),
            )
            next_y = title_y + th + int(canvas_h * 0.01)
        else:
            next_y = int(canvas_h * 0.04)

        if spec.subtitle:
            sw, sh = _text_size(draw, spec.subtitle, subtitle_font)
            sx = (canvas_w - sw) // 2
            draw.text(
                (sx, next_y),
                spec.subtitle,
                font=subtitle_font,
                fill=(90, 90, 90),
            )

    def _paste_item(self, canvas: Image.Image, item: PlacedItem) -> None:
        with Image.open(item.master.image_path) as src:
            src = src.convert("RGBA")
            # Crop to the tight alpha bbox so the resized output is exactly
            # the fish silhouette at exactly the engine's chosen draw_width
            # × draw_height. Without this crop, the resize stuffs the master
            # canvas (with its huge transparent margins) into the draw box,
            # and the visible fish ends up at a fraction of the requested
            # size. Bug B fix.
            bbox = item.master.alpha_bbox
            if bbox is not None:
                src = src.crop(bbox)
            resized = src.resize(
                (max(1, item.draw_width), max(1, item.draw_height)),
                resample=Image.Resampling.LANCZOS,
            )

        # Use the alpha channel as a mask if the source has meaningful
        # transparency. Otherwise, pasting directly is faster and preserves
        # subpixel white detail on the white background.
        alpha = resized.getchannel("A")
        if alpha.getextrema()[0] < 255:
            canvas.paste(resized, (item.x, item.y), mask=alpha)
        else:
            canvas.paste(resized.convert("RGB"), (item.x, item.y))

    def _draw_label(
        self,
        draw: ImageDraw.ImageDraw,
        item: PlacedItem,
        label_font: ImageFont.ImageFont,
    ) -> None:
        text = item.species_ref.common_name
        tw, th = _text_size(draw, text, label_font)
        cx = item.x + item.draw_width // 2
        ly = item.y + item.draw_height + max(6, th // 3)
        draw.text(
            (cx - tw // 2, ly),
            text,
            font=label_font,
            fill=(40, 40, 40),
        )


# --- EditorialPosterRenderer -------------------------------------------------


class EditorialPosterRenderer(PosterRenderer):
    """Editorial-grade single-subject poster renderer.

    Layout assumptions:

    - Exactly one ``PlacedItem`` in the ``LayoutResult`` (produced by
      ``HeroLayoutEngine``). If zero, the render aborts with a log and
      only the background is drawn. If more than one, the first is used.
    - The canvas is portrait-oriented (works on landscape too, but
      typographic proportions assume portrait).
    - ``spec.background_color`` is honored verbatim (default ``#FFFFFF``
      comes from ``PosterSpec``). Pass any hex string at the CLI via
      ``--background`` to change the poster backdrop color.
    """

    # Default colors. DEFAULT_BACKGROUND is only used as a last-resort
    # fallback when spec.background_color is empty; the normal path is for
    # the spec to carry whatever color the caller wants.
    DEFAULT_BACKGROUND = "#FFFFFF"
    DEFAULT_TITLE_INK = "#1a1612"
    DEFAULT_SCIENTIFIC_INK = "#5a5248"
    DEFAULT_RULE_INK = "#8a7f72"

    def __init__(
        self,
        title_font_size: int = 150,
        scientific_font_size: int = 52,
        subtitle_font_size: int = 36,
        caption_font_size: int = 28,
        font_candidates: tuple[str, ...] = _EDITORIAL_FONT_CANDIDATES,
        title_color: str = DEFAULT_TITLE_INK,
        scientific_color: str = DEFAULT_SCIENTIFIC_INK,
        rule_color: str = DEFAULT_RULE_INK,
        caption_color: str | None = None,
        subtitle_letter_spacing: int = 6,
        caption_letter_spacing: int = 4,
    ) -> None:
        self.title_font_size = title_font_size
        self.scientific_font_size = scientific_font_size
        self.subtitle_font_size = subtitle_font_size
        self.caption_font_size = caption_font_size
        self.font_candidates = font_candidates
        self.title_color = title_color
        self.scientific_color = scientific_color
        self.rule_color = rule_color
        self.caption_color = caption_color or rule_color
        self.subtitle_letter_spacing = subtitle_letter_spacing
        self.caption_letter_spacing = caption_letter_spacing

    # ------------------------------------------------------------------ public

    def render(self, result: LayoutResult, output_path: Path) -> None:
        spec = result.poster

        # Honor spec.background_color verbatim so --background on the CLI
        # can put the subject on any backdrop (white, navy, sage, etc).
        # Fall back only if the spec left the field blank.
        bg = spec.background_color or self.DEFAULT_BACKGROUND

        # Adaptive palette — flip text colors to warm-off-white on dark
        # backgrounds so Didot stays legible regardless of bg choice.
        title_c, sci_c, rule_c, caption_c = _adaptive_palette(bg)
        self.title_color = title_c
        self.scientific_color = sci_c
        self.rule_color = rule_c
        self.caption_color = caption_c
        logger.info(
            "editorial background: %s (luminance=%.0f, title ink=%s)",
            bg,
            _relative_luminance(bg),
            title_c,
        )

        canvas = Image.new("RGB", (spec.canvas_width, spec.canvas_height), color=bg)
        draw = ImageDraw.Draw(canvas)

        if not result.placements:
            logger.warning(
                "EditorialPosterRenderer got zero placements; rendering "
                "background-only poster."
            )
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path, format="PNG")
            return

        placed = result.placements[0]
        species_ref: SpeciesRef = placed.species_ref

        # Load font family lazily now (not at __init__ time).
        title_font, scientific_font, regular_font = self._load_font_family(
            self.font_candidates,
            title_size=self.title_font_size,
            scientific_size=self.scientific_font_size,
            regular_size=self.subtitle_font_size,
        )
        caption_font = self._load_regular_only(
            self.font_candidates, size=self.caption_font_size
        )

        # 1. Title band.
        self._draw_title_band(
            draw=draw,
            canvas_w=spec.canvas_width,
            canvas_h=spec.canvas_height,
            spec=spec,
            species_ref=species_ref,
            title_font=title_font,
            scientific_font=scientific_font,
        )

        # 2. Hero image.
        self._paste_hero(canvas, placed)

        # 3. Caption band.
        self._draw_caption_band(
            draw=draw,
            canvas_w=spec.canvas_width,
            canvas_h=spec.canvas_height,
            spec=spec,
            species_ref=species_ref,
            subtitle_font=regular_font,
            caption_font=caption_font,
        )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG")

    # ------------------------------------------------------------- font helper
    #
    # The real implementations live as module-level helpers so the
    # multi-species editorial renderer can share them. These thin
    # staticmethod wrappers preserve the historical public API of this
    # class (anything calling ``EditorialPosterRenderer._load_font_family``
    # etc. directly will still work).

    @staticmethod
    def _identify_ttc_faces(
        path: str, size: int
    ) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
        return _identify_ttc_faces(path, size)

    @staticmethod
    def _load_font_family(
        paths: list[str] | tuple[str, ...],
        title_size: int,
        scientific_size: int,
        regular_size: int,
    ) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
        return _load_display_fonts(paths, title_size, scientific_size, regular_size)

    @staticmethod
    def _load_regular_only(
        paths: list[str] | tuple[str, ...], size: int
    ) -> ImageFont.ImageFont:
        return _load_caption_font(paths, size)

    # ------------------------------------------------------------- band: title

    def _draw_title_band(
        self,
        draw: ImageDraw.ImageDraw,
        canvas_w: int,
        canvas_h: int,
        spec: PosterSpec,
        species_ref: SpeciesRef,
        title_font: ImageFont.ImageFont,
        scientific_font: ImageFont.ImageFont,
    ) -> None:
        # 20% title band is the long-standing single-subject value; the
        # multi renderer uses 16%. Keeping this constant preserves byte-
        # identical Smallmouth output.
        title_band_h = int(round(canvas_h * 0.20))
        title_text = spec.title or species_ref.common_name
        sci_text = species_ref.scientific_name or ""
        _draw_title_block(
            draw=draw,
            canvas_w=canvas_w,
            title_band_h=title_band_h,
            title=title_text,
            italic_secondary=sci_text,
            bold_font=title_font,
            italic_font=scientific_font,
            title_color=self.title_color,
            secondary_color=self.scientific_color,
            rule_color=self.rule_color,
        )

    def _draw_ornamental_rule(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        canvas_width: int,
        color: str,
    ) -> None:
        """Thin horizontal rule with a small centered diamond break."""
        _draw_ornamental_rule(
            draw=draw, y=y, canvas_width=canvas_width, color=color
        )

    # -------------------------------------------------------------- band: hero

    def _paste_hero(self, canvas: Image.Image, placed: PlacedItem) -> None:
        with Image.open(placed.master.image_path) as src:
            src = src.convert("RGBA")
            bbox = placed.master.alpha_bbox
            if bbox is not None:
                src = src.crop(bbox)
            resized = src.resize(
                (max(1, placed.draw_width), max(1, placed.draw_height)),
                resample=Image.Resampling.LANCZOS,
            )

        alpha = resized.getchannel("A")
        if alpha.getextrema()[0] < 255:
            canvas.paste(resized, (placed.x, placed.y), mask=alpha)
        else:
            canvas.paste(resized.convert("RGB"), (placed.x, placed.y))

    # ----------------------------------------------------------- band: caption

    def _draw_caption_band(
        self,
        draw: ImageDraw.ImageDraw,
        canvas_w: int,
        canvas_h: int,
        spec: PosterSpec,
        species_ref: SpeciesRef,
        subtitle_font: ImageFont.ImageFont,
        caption_font: ImageFont.ImageFont,
    ) -> None:
        # Preserve the historical 0.85 split (caption band starts 85% down).
        caption_band_top = int(round(canvas_h * 0.85))
        caption_band_h = canvas_h - caption_band_top

        # Subtitle text: spec.subtitle, else joined habitat tags.
        subtitle_text = spec.subtitle
        if not subtitle_text:
            tags = species_ref.habitat_tags[:3] if species_ref.habitat_tags else []
            subtitle_text = " \u00b7 ".join(t.title() for t in tags) if tags else ""

        # Secondary caption: e.g. "MICROPTERUS DOLOMIEU · FRESHWATER".
        secondary_parts: list[str] = []
        if species_ref.scientific_name:
            secondary_parts.append(species_ref.scientific_name.upper())
        if species_ref.habitat_tags:
            secondary_parts.append(species_ref.habitat_tags[0].upper())
        secondary = " \u00b7 ".join(secondary_parts) if secondary_parts else None

        _draw_caption_block(
            draw=draw,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            caption_band_h=caption_band_h,
            primary_text=subtitle_text,
            secondary_text=secondary,
            primary_font=subtitle_font,
            secondary_font=caption_font,
            color=self.caption_color,
            primary_letter_spacing=self.subtitle_letter_spacing,
            secondary_letter_spacing=self.caption_letter_spacing,
        )

    def _draw_tracked_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        y: int,
        canvas_w: int,
        tracking_px: int,
        fill: str,
    ) -> None:
        """Draw ``text`` centered at ``y`` with extra pixels between glyphs."""
        _draw_tracked_text(
            draw=draw,
            text=text,
            font=font,
            y=y,
            canvas_w=canvas_w,
            tracking_px=tracking_px,
            fill=fill,
        )


# --- EditorialMultiRenderer --------------------------------------------------


class EditorialMultiRenderer(PosterRenderer):
    """Editorial multi-species poster renderer.

    Pairs with :class:`SmallEnsembleLayoutEngine` (Phase 1) and the future
    ``FieldGuideLayoutEngine`` (Phase 2). Reuses the Didot fallback chain,
    luminance-adaptive palette, ornamental rule, and title/caption bands
    from :class:`EditorialPosterRenderer` via shared module-level helpers,
    then adds a per-species italic Didot label under each placed master.

    Honors ``spec.background_color`` exactly — no hardcoded ivory — and
    flips text colors via :func:`_adaptive_palette` so the page reads
    cleanly on white, navy, sage, etc.
    """

    DEFAULT_BACKGROUND = EditorialPosterRenderer.DEFAULT_BACKGROUND
    DEFAULT_TITLE_INK = EditorialPosterRenderer.DEFAULT_TITLE_INK
    DEFAULT_SCIENTIFIC_INK = EditorialPosterRenderer.DEFAULT_SCIENTIFIC_INK
    DEFAULT_RULE_INK = EditorialPosterRenderer.DEFAULT_RULE_INK

    def __init__(
        self,
        title_font_size: int = 130,
        scientific_font_size: int = 46,
        subtitle_font_size: int = 36,
        caption_font_size: int = 28,
        label_common_font_size: int = 42,
        label_scientific_font_size: int = 32,
        label_gap_px: int = 10,
        font_candidates: tuple[str, ...] = _EDITORIAL_FONT_CANDIDATES,
        title_color: str = DEFAULT_TITLE_INK,
        scientific_color: str = DEFAULT_SCIENTIFIC_INK,
        rule_color: str = DEFAULT_RULE_INK,
        caption_color: str | None = None,
        subtitle_letter_spacing: int = 6,
        caption_letter_spacing: int = 4,
        label_letter_spacing: int = 4,
        style_profile=None,
    ) -> None:
        self.title_font_size = title_font_size
        self.scientific_font_size = scientific_font_size
        self.subtitle_font_size = subtitle_font_size
        self.caption_font_size = caption_font_size
        self.label_common_font_size = label_common_font_size
        self.label_scientific_font_size = label_scientific_font_size
        self.label_gap_px = label_gap_px
        self.font_candidates = font_candidates
        self.title_color = title_color
        self.scientific_color = scientific_color
        self.rule_color = rule_color
        self.caption_color = caption_color or rule_color
        self.subtitle_letter_spacing = subtitle_letter_spacing
        self.caption_letter_spacing = caption_letter_spacing
        self.label_letter_spacing = label_letter_spacing
        self.border_plants: bool = False
        self._background_image_path: Path | None = None
        self.background_dim: float = 0.15
        # Leader-line mode: place labels in whitespace with thin connector
        # lines. When False, labels sit directly below species (original).
        self.leader_line_labels: bool = True
        self.leader_line_width: int = 1
        self.leader_max_length: float = 0.12
        # Custom font overrides for user-edited posters. When these paths
        # are set, they take precedence over the default Didot fallback chain.
        self._custom_title_font_path: Path | None = None
        self._custom_label_font_path: Path | None = None
        # Label color override — when set, labels use this color instead of
        # the auto-computed adaptive palette.
        self._label_override_color: str | None = None
        # When True, the adaptive palette is skipped — user's explicit
        # title/scientific/label colors are used as-is.
        self._disable_adaptive_palette: bool = False
        # Crop each master image to its alpha bbox before resize, so the
        # silhouette's aspect is preserved instead of stretching the
        # full-frame master.
        self._crop_master_to_alpha: bool = True
        # Reference-aesthetic toggles (Task B/C/D). When the editor or API
        # requests the new poster look, these switches enable: a two-line
        # preheader+title block, the cream paper background, the thin inner
        # border, common-name-only labels, and a wood frame composited on
        # the output. Defaults are ON (the new look becomes the default).
        self._use_two_line_title: bool = True
        self._preheader_text: str = "FISH OF"
        self._show_scientific_names: bool = False
        self._inner_border_enabled: bool = True
        self._paper_grain_enabled: bool = True
        self._frame_style: str | None = None  # None | walnut|oak|black|white
        # Style profile (Field Guide / Vintage Tackle / Classic). When
        # supplied, its values OVERRIDE the toggles above so a single
        # profile decision configures every aesthetic knob coherently.
        # When None, all toggles keep the historical defaults — strict
        # back-compat for callers that haven't migrated yet.
        self._style_profile = style_profile
        if style_profile is not None:
            tk = style_profile.title_kind
            self._use_two_line_title = tk == "two_line"
            self._inner_border_enabled = style_profile.inner_border != "none"
            self._paper_grain_enabled = style_profile.paper_grain_enabled
            # label_kind drives leader_line_labels (set later, below).
            self.leader_line_labels = style_profile.label_kind == "leader_lines"
            # Show scientific names inline when the profile asks for the
            # two-line label.
            self._show_scientific_names = (
                style_profile.label_kind == "common_plus_latin_italic"
            )

    def render(self, result: LayoutResult, output_path: Path) -> None:
        spec = result.poster

        bg = spec.background_color or self.DEFAULT_BACKGROUND
        # Style profile: when supplied, the profile's paper_hex is the
        # source of truth UNLESS the caller passed a non-default background
        # (i.e. a user customization). We treat plain white / default as
        # "no opinion, use the profile" to avoid breaking older callers.
        _profile = getattr(self, "_style_profile", None)
        if _profile is not None and isinstance(bg, str):
            if bg.lower() in ("#ffffff", "#fff", "white", self.DEFAULT_BACKGROUND.lower()):
                bg = _profile.paper_hex
        # Reference aesthetic: when the caller passed plain white (the
        # historical default), substitute the cream paper color so the
        # poster looks like the field-guide reference out of the box.
        # Anyone who explicitly picked a non-white background still gets it.
        if (
            _profile is None
            and getattr(self, "_use_two_line_title", False)
            and isinstance(bg, str)
            and bg.lower() in ("#ffffff", "#fff", "white")
        ):
            bg = REFERENCE_PAPER_HEX

        # If a background image is supplied, build the canvas from it
        # (object-fit: cover), then derive the text palette from the
        # sampled luminance of the title band so Didot stays legible.
        bg_image_canvas: Image.Image | None = None
        sampled_luminance: float | None = None
        if getattr(self, "_background_image_path", None):
            try:
                with Image.open(self._background_image_path) as src:
                    src = src.convert("RGB")
                    src_w, src_h = src.size
                    canvas_w0, canvas_h0 = spec.canvas_width, spec.canvas_height
                    scale = max(canvas_w0 / src_w, canvas_h0 / src_h)
                    new_w = max(1, int(round(src_w * scale)))
                    new_h = max(1, int(round(src_h * scale)))
                    resized = src.resize((new_w, new_h), Image.LANCZOS)
                    left = (new_w - canvas_w0) // 2
                    top = (new_h - canvas_h0) // 2
                    bg_image_canvas = resized.crop(
                        (left, top, left + canvas_w0, top + canvas_h0)
                    )
                    dim = max(0.0, min(1.0, float(self.background_dim)))
                    if dim > 0:
                        overlay = Image.new(
                            "RGB", bg_image_canvas.size, (255, 255, 255)
                        )
                        bg_image_canvas = Image.blend(
                            bg_image_canvas, overlay, dim * 0.3
                        )
                    import numpy as _np
                    top_slice = _np.asarray(
                        bg_image_canvas.crop(
                            (0, 0, canvas_w0, max(1, int(canvas_h0 * 0.15)))
                        )
                    )
                    r = top_slice[..., 0].astype("float32")
                    g = top_slice[..., 1].astype("float32")
                    b = top_slice[..., 2].astype("float32")
                    sampled_luminance = float(
                        (0.2126 * r + 0.7152 * g + 0.0722 * b).mean()
                    )
                    logger.info(
                        "background image: %s sampled title luminance=%.0f",
                        self._background_image_path, sampled_luminance,
                    )
            except (OSError, Image.UnidentifiedImageError) as exc:
                logger.warning(
                    "Could not load background image %s: %s",
                    self._background_image_path, exc,
                )
                bg_image_canvas = None

        if self._disable_adaptive_palette:
            # Keep the explicit colors the caller set (user edits)
            pass
        else:
            if sampled_luminance is not None:
                grey = max(0, min(255, int(round(sampled_luminance))))
                synth = f"#{grey:02x}{grey:02x}{grey:02x}"
                title_c, sci_c, rule_c, caption_c = _adaptive_palette(synth)
            else:
                title_c, sci_c, rule_c, caption_c = _adaptive_palette(bg)
            self.title_color = title_c
            self.scientific_color = sci_c
            self.rule_color = rule_c
            self.caption_color = caption_c
            # Reference aesthetic: lock title to deep warm brown when on a
            # cream paper background (matches the reference poster — looks
            # warmer than the default Didot-black "#1a1612").
            if (
                getattr(self, "_use_two_line_title", False)
                and bg_image_canvas is None
                and _relative_luminance(bg) > 200
            ):
                self.title_color = REFERENCE_INK_HEX
                self.scientific_color = REFERENCE_INK_HEX
            # Style profile inks override the adaptive palette so each
            # named style has a coherent identity (warm-brown for Vintage
            # Tackle, near-black charcoal for Field Guide).
            if _profile is not None and bg_image_canvas is None:
                self.title_color = _profile.ink_hex
                self.scientific_color = _profile.ink_hex
                self.rule_color = _profile.rule_hex
                self.caption_color = _profile.ink_hex
        logger.info(
            "editorial-multi background: %s (luminance=%.0f, title ink=%s)",
            bg,
            _relative_luminance(bg),
            self.title_color,
        )

        if bg_image_canvas is not None:
            canvas = bg_image_canvas.copy()
        else:
            canvas = Image.new(
                "RGB", (spec.canvas_width, spec.canvas_height), color=bg
            )

        # Paper grain (Task C): subtle noise texture over the cream
        # background so the page reads as paper, not flat color. Only when
        # there's no background image AND the toggle is on — we don't want
        # to fight a user-supplied photographic background.
        if (
            getattr(self, "_paper_grain_enabled", False)
            and bg_image_canvas is None
        ):
            grain_intensity = (
                _profile.paper_grain_intensity if _profile is not None else 0.10
            )
            _apply_paper_grain(canvas, intensity=grain_intensity)

        draw = ImageDraw.Draw(canvas)

        # Inner border (Task C): branch on the style profile's inner_border
        # mode so the two decorations are mutually exclusive (prior code
        # drew both the thin rectangle AND the diamond accent for
        # vintage_tackle, producing a double border).
        #   - "thin"            → draw _draw_inner_border only
        #   - "double_diamond"  → draw the ornamental diamond corners only
        #   - "none"            → draw nothing
        if getattr(self, "_inner_border_enabled", False):
            border_color = (
                _profile.rule_hex if _profile is not None
                else REFERENCE_INNER_BORDER_HEX
            )
            inner_border_mode = (
                _profile.inner_border if _profile is not None else "thin"
            )
            if inner_border_mode == "double_diamond":
                try:
                    self._draw_double_diamond_accent(
                        draw=draw,
                        canvas_w=spec.canvas_width,
                        canvas_h=spec.canvas_height,
                        color=_profile.accent_hex,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Double-diamond accent draw failed: %s", exc)
            elif inner_border_mode == "thin":
                try:
                    _draw_inner_border(
                        draw=draw,
                        canvas_w=spec.canvas_width,
                        canvas_h=spec.canvas_height,
                        color=border_color,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Inner border draw failed: %s", exc)

        if not result.placements:
            logger.warning(
                "EditorialMultiRenderer got zero placements; rendering "
                "background-only poster."
            )
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Apply frame even on empty posters so the user sees the frame
            # picker working at full size.
            final = canvas
            if getattr(self, "_frame_style", None):
                final = _compose_with_frame(canvas, self._frame_style)
            final.save(output_path, format="PNG")
            return

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # Match SmallEnsembleLayoutEngine's default fractions so the title
        # and caption bands frame the species area cleanly.
        title_band_h = int(round(canvas_h * 0.16))
        caption_band_h = int(round(canvas_h * 0.10))

        # Load fonts (lazy). If the user chose a custom font, load it from
        # assets/fonts/ for both title and labels; otherwise fall back to
        # the Didot family chain.
        if self._custom_title_font_path and self._custom_title_font_path.exists():
            try:
                tfp = str(self._custom_title_font_path)
                title_font = ImageFont.truetype(tfp, self.title_font_size)
                scientific_font = ImageFont.truetype(tfp, self.scientific_font_size)
                regular_font = ImageFont.truetype(tfp, self.subtitle_font_size)
            except Exception:
                title_font, scientific_font, regular_font = _load_display_fonts(
                    self.font_candidates,
                    title_size=self.title_font_size,
                    scientific_size=self.scientific_font_size,
                    regular_size=self.subtitle_font_size,
                )
        else:
            title_font, scientific_font, regular_font = _load_display_fonts(
                self.font_candidates,
                title_size=self.title_font_size,
                scientific_size=self.scientific_font_size,
                regular_size=self.subtitle_font_size,
            )
        caption_font = _load_caption_font(
            self.font_candidates, size=self.caption_font_size
        )
        # Label fonts: italic (scientific) and regular (common name).
        if self._custom_label_font_path and self._custom_label_font_path.exists():
            try:
                lfp = str(self._custom_label_font_path)
                label_italic_font = ImageFont.truetype(lfp, self.label_scientific_font_size)
                label_common_regular = ImageFont.truetype(lfp, self.label_common_font_size)
            except Exception:
                _, label_italic_font, _ = _load_display_fonts(
                    self.font_candidates,
                    title_size=self.label_scientific_font_size,
                    scientific_size=self.label_scientific_font_size,
                    regular_size=self.label_scientific_font_size,
                )
                label_common_regular = _load_caption_font(
                    self.font_candidates, size=self.label_common_font_size
                )
        else:
            _, label_italic_font, _ = _load_display_fonts(
                self.font_candidates,
                title_size=self.label_scientific_font_size,
                scientific_size=self.label_scientific_font_size,
                regular_size=self.label_scientific_font_size,
            )
            label_common_regular = _load_caption_font(
                self.font_candidates, size=self.label_common_font_size
            )

        # 1. Title block — title + italic subtitle (or empty) + ornamental rule.
        # If the editor passed explicit (x_frac, y_frac) for the title group,
        # render the title + subtitle at those fractional canvas coords (no
        # ornamental rule, since the user picked their own placement).
        _title_xf = getattr(self, "_title_x_frac", None)
        _title_yf = getattr(self, "_title_y_frac", None)
        if _title_xf is not None and _title_yf is not None:
            title_text = spec.title or ""
            tx_origin = int(round(float(_title_xf) * canvas_w))
            ty_origin = int(round(float(_title_yf) * canvas_h))
            sub_text = spec.subtitle or ""
            cur_y = ty_origin
            if title_text:
                tw, th = _text_size(draw, title_text, title_font)
                # Center horizontally within the canvas, but offset by the
                # group's left position (tx_origin acts like left edge of a
                # canvas-wide centering box — same convention as client).
                title_x = tx_origin + (canvas_w - tw) // 2
                draw.text(
                    (title_x, cur_y),
                    title_text,
                    font=title_font,
                    fill=self.title_color,
                )
                cur_y += th + int(self.title_font_size * 0.4)
            if sub_text:
                sw, sh = _text_size(draw, sub_text, scientific_font)
                sub_x = tx_origin + (canvas_w - sw) // 2
                draw.text(
                    (sub_x, cur_y),
                    sub_text,
                    font=scientific_font,
                    fill=self.scientific_color,
                )
        elif (
            getattr(self, "_style_profile", None) is not None
            and getattr(self._style_profile, "title_kind", None) == "ornamental_frame"
        ):
            # Vintage-tackle catalog title: preheader, top rule with center
            # diamond glyph, giant title, bottom rule with diamond.
            try:
                _draw_ornamental_title_frame(
                    draw=draw,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    preheader_text=getattr(self, "_preheader_text", "FISH OF"),
                    main_title=spec.title or "",
                    title_color=self._style_profile.ink_hex,
                    rule_color=self._style_profile.rule_hex,
                    accent_color=self._style_profile.accent_hex,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Ornamental title frame draw failed (%s); falling back.", exc
                )
                _draw_title_block(
                    draw=draw,
                    canvas_w=canvas_w,
                    title_band_h=title_band_h,
                    title=spec.title or "",
                    italic_secondary=spec.subtitle,
                    bold_font=title_font,
                    italic_font=scientific_font,
                    title_color=self.title_color,
                    secondary_color=self.scientific_color,
                    rule_color=self.rule_color,
                )
        elif getattr(self, "_use_two_line_title", False):
            # Reference-aesthetic two-line title: tracked preheader flanked
            # by horizontal rules + big transitional-serif title beneath.
            try:
                _draw_two_line_title(
                    draw=draw,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    preheader_text=getattr(self, "_preheader_text", "FISH OF"),
                    main_title=spec.title or "",
                    title_color=self.title_color,
                    rule_color=REFERENCE_INNER_BORDER_HEX,
                    title_band_h=title_band_h,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Two-line title draw failed (%s); falling back to default block.",
                    exc,
                )
                _draw_title_block(
                    draw=draw,
                    canvas_w=canvas_w,
                    title_band_h=title_band_h,
                    title=spec.title or "",
                    italic_secondary=spec.subtitle,
                    bold_font=title_font,
                    italic_font=scientific_font,
                    title_color=self.title_color,
                    secondary_color=self.scientific_color,
                    rule_color=self.rule_color,
                )
        else:
            _draw_title_block(
                draw=draw,
                canvas_w=canvas_w,
                title_band_h=title_band_h,
                title=spec.title or "",
                italic_secondary=spec.subtitle,
                bold_font=title_font,
                italic_font=scientific_font,
                title_color=self.title_color,
                secondary_color=self.scientific_color,
                rule_color=self.rule_color,
            )

        # 2. Paste each placed item and draw its label.
        # Detect shelf density: group placements by y-position (±80px)
        # and use a smaller label font for crowded shelves (>4 items).
        shelves: dict[int, list[PlacedItem]] = {}
        for placed in result.placements:
            bucket = placed.y // 80 * 80
            shelves.setdefault(bucket, []).append(placed)

        # Pre-load density-scaled label fonts.
        dense_common = label_common_regular
        dense_italic = label_italic_font
        dense_spacing = self.label_letter_spacing
        for bucket, shelf_items in shelves.items():
            if len(shelf_items) > 4:
                scale = max(0.55, 4.0 / len(shelf_items))
                dense_size_c = max(14, int(self.label_common_font_size * scale))
                dense_size_s = max(12, int(self.label_scientific_font_size * scale))
                _, dense_italic, dense_common = _load_display_fonts(
                    self.font_candidates, dense_size_c, dense_size_s, dense_size_c
                )
                dense_spacing = max(1, int(self.label_letter_spacing * scale))
                break  # one dense-font set is enough for all dense shelves

        for placed in result.placements:
            self._paste_item(canvas, placed)

        # Style-profile label kinds take precedence over the legacy
        # leader-lines / inline-below toggle. The profile sets
        # `label_kind` to one of: "tracked_common_only" (Field Guide),
        # "common_plus_latin_italic" (Vintage Tackle), or "leader_lines"
        # (Classic). When no profile is set, fall through to the legacy
        # behavior — strict back-compat.
        _profile = getattr(self, "_style_profile", None)
        _label_kind = getattr(_profile, "label_kind", None) if _profile else None

        if _label_kind == "tracked_common_only":
            # Tracked uppercase common-name caption centered under each
            # fish — the field-guide reference look. Bumped to 0.018 (was
            # 0.011) so labels read at viewing distance — the previous
            # size was sub-legible.
            common_size = max(34, int(round(spec.canvas_height * 0.018)))
            label_font = _load_caption_font(self.font_candidates, common_size)
            _draw_compact_caption_only(
                draw=draw,
                placements=result.placements,
                font=label_font,
                ink_hex=_profile.ink_hex,
                canvas_h=spec.canvas_height,
                label_letter_spacing=max(2, int(round(common_size * 0.18))),
            )
        elif _label_kind == "common_plus_latin_italic":
            # Two-line label: bold common name + italic Latin scientific.
            common_size = max(34, int(round(spec.canvas_height * 0.020)))
            latin_size = max(28, int(round(spec.canvas_height * 0.016)))
            _, italic_font, body_font = _load_display_fonts(
                self.font_candidates,
                title_size=common_size,
                scientific_size=latin_size,
                regular_size=common_size,
            )
            _draw_two_line_label(
                draw=draw,
                placements=result.placements,
                body_font=body_font,
                italic_font=italic_font,
                ink_hex=_profile.ink_hex,
                canvas_h=spec.canvas_height,
            )
        elif self.leader_line_labels:
            self._draw_labels_with_leaders(
                canvas=canvas,
                draw=draw,
                result=result,
                common_font=label_common_regular,
                scientific_font=label_italic_font,
                label_color=self.title_color,
                scientific_color=self.scientific_color,
                title_band_h=title_band_h,
                caption_band_h=caption_band_h,
            )
        else:
            # Inline-below labels with hard collision avoidance. Compute
            # each label's REAL rect using actual font metrics for the
            # rendered text, then walk placements and shift any colliding
            # label DOWN (then LEFT/RIGHT as last-resort) until its AABB
            # does not intersect any already-placed label's AABB nor any
            # other species' silhouette bbox. This is the fix for the
            # BLACK CRAPPIE / YELLOW PERCH overlap visible in the editor's
            # /api/render-custom path (which sets leader_line_labels=False
            # to preserve user drag positions).
            _label_color = self._label_override_color or self.title_color
            _label_sci_color = self._label_override_color or self.scientific_color
            label_pad_px = max(8, int(self.label_letter_spacing * 2))
            label_gap_px = self.label_gap_px
            show_sci_inline = bool(getattr(self, "_show_scientific_names", True))

            def _measure_label_dims(
                placed: PlacedItem, common_f, scientific_f, common_spacing
            ) -> tuple[int, int]:
                sp = placed.species_ref
                common = (sp.common_name or "").upper()
                sci = (sp.scientific_name or "") if show_sci_inline else ""
                cw = ch = 0
                if common:
                    advances = [_text_size(draw, c, common_f)[0] for c in common]
                    cw = sum(advances) + common_spacing * max(0, len(common) - 1)
                    _, ch = _text_size(draw, common, common_f)
                sw = sh = 0
                if sci:
                    sw, sh = _text_size(draw, sci, scientific_f)
                gap = max(6, ch // 4) if (common and sci) else 0
                lw = max(cw, sw)
                lh = ch + gap + sh
                return max(0, lw), max(0, lh)

            # Plan all label positions first.
            placed_label_rects: list[tuple[int, int, int, int]] = []
            silhouette_rects: list[tuple[int, int, int, int]] = [
                (p.x, p.y, p.x + p.draw_width, p.y + p.draw_height)
                for p in result.placements
            ]

            def _label_collides(lx: int, ly: int, lw: int, lh: int,
                                self_idx: int) -> bool:
                ax1, ay1 = lx - label_pad_px, ly - label_pad_px
                ax2, ay2 = lx + lw + label_pad_px, ly + lh + label_pad_px
                for rx1, ry1, rx2, ry2 in placed_label_rects:
                    if ax1 < rx2 and ax2 > rx1 and ay1 < ry2 and ay2 > ry1:
                        return True
                for i, (sx1, sy1, sx2, sy2) in enumerate(silhouette_rects):
                    if i == self_idx:
                        continue
                    if ax1 < sx2 and ax2 > sx1 and ay1 < sy2 and ay2 > sy1:
                        return True
                return False

            # Place largest-first so smaller species' labels yield to bigger.
            order = sorted(
                range(len(result.placements)),
                key=lambda i: -(result.placements[i].draw_width
                                * result.placements[i].draw_height),
            )
            label_positions: dict[int, tuple[int, int, int, int]] = {}  # idx -> (x,y,lw,lh)
            for idx in order:
                placed = result.placements[idx]
                bucket = placed.y // 80 * 80
                is_dense = len(shelves.get(bucket, [])) > 4
                cf = dense_common if is_dense else label_common_regular
                sf = dense_italic if is_dense else label_italic_font
                csp = dense_spacing if is_dense else self.label_letter_spacing
                lw, lh = _measure_label_dims(placed, cf, sf, csp)
                if lw <= 0 or lh <= 0:
                    continue
                cx = placed.x + placed.draw_width // 2
                lx0 = cx - lw // 2
                ly0 = placed.y + placed.draw_height + label_gap_px
                # Walk DOWN in steps; if we run off the canvas, walk LEFT or
                # RIGHT (label centered under fish but offset).
                step = max(8, lh // 4)
                chosen = None
                # Try sliding down first.
                ly = ly0
                guard = 0
                while ly + lh < spec.canvas_height and guard < 200:
                    if not _label_collides(lx0, ly, lw, lh, idx):
                        chosen = (lx0, ly)
                        break
                    ly += step
                    guard += 1
                # If no slot below, try just above the silhouette.
                if chosen is None:
                    ly = placed.y - lh - label_gap_px
                    guard = 0
                    while ly > 0 and guard < 200:
                        if not _label_collides(lx0, ly, lw, lh, idx):
                            chosen = (lx0, ly)
                            break
                        ly -= step
                        guard += 1
                # Last resort: keep at default y but shift X.
                if chosen is None:
                    for dx in range(0, spec.canvas_width // 2, max(16, lw // 4)):
                        for sign in (1, -1):
                            tx = lx0 + sign * dx
                            if tx < 0 or tx + lw > spec.canvas_width:
                                continue
                            if not _label_collides(tx, ly0, lw, lh, idx):
                                chosen = (tx, ly0)
                                break
                        if chosen is not None:
                            break
                if chosen is None:
                    chosen = (lx0, ly0)  # accept overlap as absolute fallback
                lx, ly = chosen
                label_positions[idx] = (lx, ly, lw, lh)
                placed_label_rects.append((lx, ly, lx + lw, ly + lh))

            # Now actually draw, in original placement order.
            from dataclasses import replace as _dc_replace
            for idx, placed in enumerate(result.placements):
                if idx not in label_positions:
                    continue
                lx, ly, lw, lh = label_positions[idx]
                bucket = placed.y // 80 * 80
                is_dense = len(shelves.get(bucket, [])) > 4
                cf = dense_common if is_dense else label_common_regular
                sf = dense_italic if is_dense else label_italic_font
                csp = dense_spacing if is_dense else self.label_letter_spacing
                # Suppress scientific name in the placed's species_ref clone
                # so _draw_species_label_at draws common-name only.
                placed_to_draw = placed
                if not show_sci_inline:
                    new_ref = _dc_replace(placed.species_ref, scientific_name="")
                    placed_to_draw = _dc_replace(placed, species_ref=new_ref)
                _draw_species_label_at(
                    draw=draw,
                    placed=placed_to_draw,
                    label_x=lx,
                    label_y=ly,
                    label_w=lw,
                    common_font=cf,
                    scientific_font=sf,
                    common_color=_label_color,
                    sci_color=_label_sci_color,
                    common_letter_spacing=csp,
                    stroke_width=getattr(self, "_label_stroke_width", 0),
                    stroke_fill=getattr(self, "_label_stroke_fill", None),
                )

        # 2b. Border plants — decorative waterline strip above caption band.
        if self.border_plants:
            self._draw_border_plants(canvas, spec, caption_band_h)

        # 3. Caption band. The subtitle is already used in the title block,
        # so populate the caption from habitat tags (or fall back to the
        # subtitle when no habitats are available) so the band isn't empty.
        # SKIP entirely when a style profile is in play — the new Field
        # Guide / Vintage Tackle layouts handle their own bottom margin
        # and a habitat-tags caption (e.g. "LAKE · STREAM") collides with
        # the species labels they draw.
        _skip_caption_band = bool(getattr(self, "_style_profile", None))
        if not _skip_caption_band:
            habitat_summary = self._build_habitat_summary(result)
            primary = habitat_summary or (spec.subtitle if not habitat_summary else None)
            secondary = None

            _draw_caption_block(
                draw=draw,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                caption_band_h=caption_band_h,
                primary_text=primary,
                secondary_text=secondary,
                primary_font=regular_font,
                secondary_font=caption_font,
                color=self.caption_color,
                primary_letter_spacing=self.subtitle_letter_spacing,
                secondary_letter_spacing=self.caption_letter_spacing,
            )

        # 4. Optional logo — composited at a configurable size & position.
        # Supports PNG (alpha) or JPEG. Defaults: 20% of canvas width,
        # bottom-center. Override via _logo_size_pct (5-40) and _logo_position
        # ("bottom-center" | "bottom-left" | "bottom-right" |
        #  "top-center" | "top-left" | "top-right").
        if hasattr(self, "_logo_path") and self._logo_path:
            try:
                with Image.open(self._logo_path) as logo_src:
                    logo = logo_src.convert("RGBA")
                    size_pct = getattr(self, "_logo_size_pct", 20) / 100.0
                    position = getattr(self, "_logo_position", "bottom-center")
                    max_logo_w = int(canvas_w * size_pct)
                    max_logo_h = int(canvas_h * size_pct * 0.4)
                    logo_scale = min(
                        max_logo_w / max(1, logo.width),
                        max_logo_h / max(1, logo.height),
                    )
                    logo_w = max(1, int(logo.width * logo_scale))
                    logo_h = max(1, int(logo.height * logo_scale))
                    logo_resized = logo.resize((logo_w, logo_h), Image.LANCZOS)
                    margin = int(canvas_w * 0.02)
                    # If explicit fractional coords are set (from a drag in the
                    # editor), they take precedence over the named position.
                    x_frac = getattr(self, "_logo_x_frac", None)
                    y_frac = getattr(self, "_logo_y_frac", None)
                    if x_frac is not None and y_frac is not None:
                        logo_x = int(round(x_frac * canvas_w))
                        logo_y = int(round(y_frac * canvas_h))
                    elif position == "bottom-center":
                        logo_x = (canvas_w - logo_w) // 2
                        logo_y = canvas_h - logo_h - margin
                    elif position == "bottom-left":
                        logo_x = margin
                        logo_y = canvas_h - logo_h - margin
                    elif position == "bottom-right":
                        logo_x = canvas_w - logo_w - margin
                        logo_y = canvas_h - logo_h - margin
                    elif position == "top-center":
                        logo_x = (canvas_w - logo_w) // 2
                        logo_y = margin
                    elif position == "top-left":
                        logo_x = margin
                        logo_y = margin
                    elif position == "top-right":
                        logo_x = canvas_w - logo_w - margin
                        logo_y = margin
                    else:
                        logo_x = (canvas_w - logo_w) // 2
                        logo_y = canvas_h - logo_h - margin
                    canvas.paste(
                        logo_resized.convert("RGB"),
                        (logo_x, logo_y),
                        mask=logo_resized.split()[3],
                    )
                    logger.info(
                        "Logo composited from %s (%dx%d at %d,%d, pos=%s)",
                        self._logo_path, logo_w, logo_h, logo_x, logo_y, position,
                    )
            except (OSError, Image.UnidentifiedImageError) as exc:
                logger.warning("Could not load logo %s: %s", self._logo_path, exc)

        # Free-preview watermark: a semi-transparent diagonal text band
        # repeated across the canvas. Drawn last so it sits on top of all
        # imagery, including the logo. Skipped when the caller has unlocked.
        if getattr(self, "_draw_watermark", False):
            try:
                self._draw_preview_watermark(canvas)
            except Exception as wm_exc:  # noqa: BLE001
                logger.warning("Watermark draw failed: %s", wm_exc)

        # Frame composition (Task B): wrap the finished poster in a
        # wood-textured frame with an inset paper mat. Applied AFTER the
        # watermark so the watermark shows on the printed area, not the
        # frame itself. The output PNG includes the frame, so the
        # downloaded poster is already framed (matches user expectation).
        final_canvas = canvas
        frame_style = getattr(self, "_frame_style", None)
        if frame_style:
            try:
                final_canvas = _compose_with_frame(canvas, frame_style)
                logger.info(
                    "Frame composed: style=%s; output dims %dx%d -> %dx%d",
                    frame_style, canvas.size[0], canvas.size[1],
                    final_canvas.size[0], final_canvas.size[1],
                )
            except Exception as fr_exc:  # noqa: BLE001
                logger.warning("Frame composition failed: %s", fr_exc)
                final_canvas = canvas

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        final_canvas.save(output_path, format="PNG")

    def _draw_preview_watermark(self, canvas: "Image.Image") -> None:
        """Tile a diagonal semi-transparent watermark across the canvas.

        Renders to a separate RGBA layer so the alpha is honoured even when
        the canvas itself is plain RGB (the editorial path produces RGB).
        """
        from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageFont as _PILFont

        text = getattr(self, "_watermark_text", None) or "PREVIEW"
        canvas_w, canvas_h = canvas.size

        # Font size scales with the canvas long edge so it reads at any size.
        font_size = max(48, int(min(canvas_w, canvas_h) * 0.06))
        font = None
        try:
            font_path = (
                Path(__file__).resolve().parent.parent
                / "assets" / "fonts" / "PlayfairDisplay-Bold.ttf"
            )
            if font_path.exists():
                font = _PILFont.truetype(str(font_path), font_size)
        except Exception:  # noqa: BLE001
            font = None
        if font is None:
            try:
                font = _PILFont.truetype("DejaVuSans-Bold.ttf", font_size)
            except Exception:  # noqa: BLE001
                font = _PILFont.load_default()

        # Draw onto an oversized transparent layer, then rotate -25° and
        # composite the visible portion back over the canvas.
        diag = int((canvas_w ** 2 + canvas_h ** 2) ** 0.5)
        layer = _PILImage.new("RGBA", (diag, diag), (0, 0, 0, 0))
        draw = _PILDraw.Draw(layer)

        # Approximate text width via textbbox.
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:  # noqa: BLE001
            tw, th = font_size * len(text) // 2, font_size

        gap_x = int(tw * 0.4)
        gap_y = int(th * 2.4)  # tighter row spacing so watermark actually tiles
        fill = (20, 20, 20, 110)  # ~43% opacity charcoal — visible but unobtrusive

        y = -th
        row = 0
        while y < diag:
            offset = (row % 2) * (tw // 2)
            x = -offset - tw
            while x < diag:
                draw.text((x, y), text, fill=fill, font=font)
                x += tw + gap_x
            y += th + gap_y
            row += 1

        rotated = layer.rotate(-25, resample=_PILImage.BICUBIC, expand=False)
        # Center-crop the rotated layer to canvas size.
        left = (rotated.width - canvas_w) // 2
        top = (rotated.height - canvas_h) // 2
        cropped = rotated.crop((left, top, left + canvas_w, top + canvas_h))

        if canvas.mode == "RGBA":
            canvas.alpha_composite(cropped)
        else:
            base = canvas.convert("RGBA")
            base.alpha_composite(cropped)
            merged = base.convert("RGB")
            canvas.paste(merged)

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _build_habitat_summary(result: LayoutResult) -> str | None:
        """Compose a small-caps habitat summary from the placed species.

        Picks the top two unique habitat tags across all placements,
        joined with " · ". Returns ``None`` if no tags are available.
        """
        seen: list[str] = []
        for placed in result.placements:
            for tag in placed.species_ref.habitat_tags or []:
                if tag and tag not in seen:
                    seen.append(tag)
                if len(seen) >= 2:
                    break
            if len(seen) >= 2:
                break
        if not seen:
            return None
        return " \u00b7 ".join(t.title() for t in seen)

    def _draw_border_plants(
        self,
        canvas: Image.Image,
        spec: PosterSpec,
        caption_band_h: int,
    ) -> None:
        """Composite decorative water-plant masters along the bottom edge.

        Creates a semi-transparent vegetation strip right above the caption
        band for a natural "waterline" divider. Skips silently when no plant
        masters are available.
        """
        from config.settings import MASTER_DIR, SPECIES_JSON

        # 1. Load species.json to find plant slugs.
        try:
            species_data = json.loads(SPECIES_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        plant_slugs = [
            s["slug"] for s in species_data
            if isinstance(s, dict) and s.get("category") == "plant" and s.get("slug")
        ]
        if not plant_slugs:
            return

        # 2. Check which plant masters exist on disk.
        plant_images: list[Image.Image] = []
        for slug in plant_slugs:
            path = MASTER_DIR / spec.style_slug / f"{slug}.png"
            if path.is_file():
                try:
                    img = Image.open(path).convert("RGBA")
                    plant_images.append(img)
                except (OSError, Image.UnidentifiedImageError):
                    continue
        if not plant_images:
            return

        # 3. Compute strip geometry.
        canvas_w, canvas_h = canvas.size
        strip_h = int(canvas_h * 0.10)
        strip_top = canvas_h - caption_band_h - strip_h // 2

        # 4. Scale each plant to fit strip_h, preserving aspect ratio.
        scaled: list[Image.Image] = []
        for img in plant_images:
            ratio = strip_h / max(1, img.height)
            new_w = max(1, int(img.width * ratio))
            new_h = strip_h
            scaled.append(img.resize((new_w, new_h), Image.LANCZOS))

        # 5. Distribute plants across the full canvas width.
        #    Repeat the available plants to fill the width with slight overlap.
        total_plant_w = sum(p.width for p in scaled)
        if total_plant_w == 0:
            return

        # Build a sequence that tiles across the canvas width.
        import random as _rng
        sequence: list[tuple[Image.Image, int, int]] = []  # (img, x, y)
        x_cursor = 0
        idx = 0
        while x_cursor < canvas_w:
            plant = scaled[idx % len(scaled)]
            # Slight vertical jitter for a natural look (±8% of strip_h).
            jitter = int(strip_h * 0.08 * (1 if idx % 2 == 0 else -1))
            y = strip_top + jitter
            sequence.append((plant, x_cursor, y))
            # Advance with slight overlap (~15% of plant width).
            x_cursor += max(1, int(plant.width * 0.85))
            idx += 1

        # 6. Composite each plant at 70% opacity.
        for plant, x, y in sequence:
            r, g, b, a = plant.split()
            plant_alpha = a.point(lambda v: int(v * 0.7))
            plant_rgb = Image.merge("RGB", (r, g, b))
            canvas.paste(plant_rgb, (x, y), mask=plant_alpha)

        # Close opened images.
        for img in plant_images:
            img.close()

    def _paste_item(self, canvas: Image.Image, placed: PlacedItem) -> None:
        with Image.open(placed.master.image_path) as src:
            src = src.convert("RGBA")
            # Crop to the alpha silhouette's bbox so resize preserves the
            # animal's actual shape instead of stretching the padded frame.
            if self._crop_master_to_alpha:
                bbox = src.split()[3].getbbox()
                if bbox:
                    src = src.crop(bbox)
            resized = src.resize(
                (max(1, placed.draw_width), max(1, placed.draw_height)),
                resample=Image.Resampling.LANCZOS,
            )
        alpha = resized.getchannel("A")
        if alpha.getextrema()[0] < 255:
            canvas.paste(resized, (placed.x, placed.y), mask=alpha)
        else:
            canvas.paste(resized.convert("RGB"), (placed.x, placed.y))

    def _draw_double_diamond_accent(
        self,
        draw: ImageDraw.ImageDraw,
        canvas_w: int,
        canvas_h: int,
        color: str,
    ) -> None:
        """Draw four ornamental diamond glyphs at the inset corner positions.

        Used by the "vintage_tackle" :class:`StyleProfile` as the *sole*
        outer border decoration (no rectangle rule). Earlier versions also
        drew an inset rectangle here, which combined with ``_draw_inner_border``
        produced two parallel borders. The render() branch now selects either
        thin-rectangle or diamond-corners — never both — so the rectangle
        was removed from this helper as well.
        """
        # Diamonds sit at ~5% inset from canvas edges (matches the prior
        # accent-rule corner positions, so existing layout math is preserved).
        inset = int(round(min(canvas_w, canvas_h) * 0.05))
        x1, y1 = inset, inset
        x2, y2 = canvas_w - inset, canvas_h - inset
        # Slightly larger than the prior corner glyphs since they now read
        # as the only border decoration.
        d = max(12, int(round(min(canvas_w, canvas_h) * 0.012)))
        for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            diamond = [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)]
            draw.polygon(diamond, fill=color)

    # -------------------------------------------------- leader-line labels
    def _draw_labels_with_leaders(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        result: LayoutResult,
        common_font: ImageFont.ImageFont,
        scientific_font: ImageFont.ImageFont,
        label_color: str,
        scientific_color: str,
        title_band_h: int,
        caption_band_h: int,
    ) -> None:
        """Place each species label in nearby whitespace with a leader line.

        Approximate occupancy via a down-scaled alpha mask for speed. For
        each species (largest first), try a prioritized list of candidate
        anchor positions; the first candidate that clears the mask wins.
        Fall back to inline-below on failure so we never crash.
        """
        import numpy as np

        spec = result.poster
        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height
        scale = 0.25
        mask_w = max(1, int(canvas_w * scale))
        mask_h = max(1, int(canvas_h * scale))
        occupancy = np.zeros((mask_h, mask_w), dtype=bool)

        # Seed occupancy with title + caption bands (labels must not intrude).
        band_top = min(mask_h, int(title_band_h * scale))
        occupancy[:band_top, :] = True
        band_bottom_start = max(0, mask_h - int(caption_band_h * scale))
        occupancy[band_bottom_start:, :] = True

        # Seed with each species silhouette (alpha > 64).
        for p in result.placements:
            try:
                with Image.open(p.master.image_path) as img:
                    alpha = img.convert("RGBA").split()[3]
                    bbox = alpha.getbbox()
                    if bbox:
                        alpha = alpha.crop(bbox)
                        # Map bbox in source to draw rect via proportional scale.
                        src_w, src_h = img.size
                    ew = max(1, int(p.draw_width * scale))
                    eh = max(1, int(p.draw_height * scale))
                    a = alpha.resize((ew, eh), Image.LANCZOS)
                    arr = np.array(a) > 64
            except Exception:  # noqa: BLE001
                continue
            ex = int(p.x * scale)
            ey = int(p.y * scale)
            ex2 = min(mask_w, ex + ew)
            ey2 = min(mask_h, ey + eh)
            ex_c = max(0, ex)
            ey_c = max(0, ey)
            if ex_c >= ex2 or ey_c >= ey2:
                continue
            sub = arr[
                ey_c - ey : ey_c - ey + (ey2 - ey_c),
                ex_c - ex : ex_c - ex + (ex2 - ex_c),
            ]
            occupancy[ey_c:ey2, ex_c:ex2] |= sub

        def _fits(lx: int, ly: int, lw: int, lh: int) -> bool:
            if lx < 0 or ly < 0 or lx + lw > canvas_w or ly + lh > canvas_h:
                return False
            mx = int(lx * scale)
            my = int(ly * scale)
            mx2 = int((lx + lw) * scale)
            my2 = int((ly + lh) * scale)
            mx = max(0, mx); my = max(0, my)
            mx2 = min(mask_w, mx2); my2 = min(mask_h, my2)
            if mx >= mx2 or my >= my2:
                return False
            region = occupancy[my:my2, mx:mx2]
            if region.size == 0:
                return False
            return (region.sum() / region.size) < 0.05

        def _mark(lx: int, ly: int, lw: int, lh: int) -> None:
            mx = max(0, int(lx * scale))
            my = max(0, int(ly * scale))
            mx2 = min(mask_w, int((lx + lw) * scale))
            my2 = min(mask_h, int((ly + lh) * scale))
            if mx < mx2 and my < my2:
                occupancy[my:my2, mx:mx2] = True

        # Reference aesthetic (Task D): suppress scientific names by default.
        # The flag is set on the renderer via `_show_scientific_names`.
        show_sci = bool(getattr(self, "_show_scientific_names", True))

        def _measure_label(sp: SpeciesRef) -> tuple[int, int, int, int]:
            """Return (label_w, label_h, common_w, common_h)."""
            common = (sp.common_name or "").upper()
            sci = (sp.scientific_name or "") if show_sci else ""
            spacing = self.label_letter_spacing
            common_w = 0
            common_h = 0
            if common:
                advances = [_text_size(draw, ch, common_font)[0] for ch in common]
                common_w = sum(advances) + spacing * max(0, len(common) - 1)
                _, common_h = _text_size(draw, common, common_font)
            sci_w = sci_h = 0
            if sci:
                sci_w, sci_h = _text_size(draw, sci, scientific_font)
            gap = max(6, common_h // 4) if common and sci else 0
            lw = max(common_w, sci_w)
            lh = common_h + gap + sci_h
            return lw, lh, common_w, common_h

        def _draw_label_block(
            lx: int, ly: int, lw: int, sp: SpeciesRef, common_w: int, common_h: int
        ) -> None:
            common = (sp.common_name or "").upper()
            spacing = self.label_letter_spacing
            cx = lx + lw // 2
            stroke_w = int(getattr(self, "_label_stroke_width", 0) or 0)
            stroke_f = getattr(self, "_label_stroke_fill", None)
            kw_common = {"font": common_font, "fill": label_color}
            kw_sci = {"font": scientific_font, "fill": scientific_color}
            if stroke_w > 0 and stroke_f:
                kw_common["stroke_width"] = stroke_w
                kw_common["stroke_fill"] = stroke_f
                kw_sci["stroke_width"] = stroke_w
                kw_sci["stroke_fill"] = stroke_f
            if common:
                advances = [_text_size(draw, ch, common_font)[0] for ch in common]
                cursor = cx - common_w // 2
                for ch, adv in zip(common, advances):
                    draw.text((cursor, ly), ch, **kw_common)
                    cursor += adv + spacing
                next_top = ly + common_h + max(6, common_h // 4)
            else:
                next_top = ly
            sci = (sp.scientific_name or "") if show_sci else ""
            if sci:
                sw, _sh = _text_size(draw, sci, scientific_font)
                draw.text((cx - sw // 2, next_top), sci, **kw_sci)

        # Place labels largest-first (so tiny species get the leftover whitespace).
        ordered = sorted(
            result.placements,
            key=lambda p: p.draw_width * p.draw_height,
            reverse=True,
        )

        gap = 20
        max_leader = int(canvas_w * self.leader_max_length)
        leader_count = 0
        fallback_count = 0

        # Track placed label rects so subsequent labels can hard-check
        # AABB-vs-AABB collision (not just the lossy occupancy mask). This
        # was the root cause of label-on-label overwrites at N>=12.
        placed_label_rects: list[tuple[int, int, int, int]] = []

        # Visual breathing room — labels that touch edge-to-edge read as one
        # word. Inflate every existing label rect by this many px in all
        # directions before testing the new candidate.
        label_pad_px = max(12, int(self.label_letter_spacing * 3))

        def _label_collides(lx: int, ly: int, lw: int, lh: int) -> bool:
            for rx1, ry1, rx2, ry2 in placed_label_rects:
                if (lx < rx2 + label_pad_px and lx + lw + label_pad_px > rx1
                        and ly < ry2 + label_pad_px and ly + lh + label_pad_px > ry1):
                    return True
            return False

        for placed in ordered:
            sp = placed.species_ref
            lw, lh, common_w, common_h = _measure_label(sp)
            if lw <= 0 or lh <= 0:
                continue
            sx, sy = placed.x, placed.y
            sw, sh = placed.draw_width, placed.draw_height
            scx = sx + sw // 2
            scy = sy + sh // 2

            candidates: list[tuple[str, int, int]] = [
                ("below_center", sx + sw // 2 - lw // 2, sy + sh + gap),
                ("right_middle", sx + sw + gap, sy + sh // 2 - lh // 2),
                ("left_middle", sx - lw - gap, sy + sh // 2 - lh // 2),
                ("above_center", sx + sw // 2 - lw // 2, sy - lh - gap),
                ("below_right", sx + sw - lw, sy + sh + gap),
                ("below_left", sx, sy + sh + gap),
            ]

            chosen: tuple[int, int] | None = None
            for _name, cx0, cy0 in candidates:
                if _fits(cx0, cy0, lw, lh) and not _label_collides(cx0, cy0, lw, lh):
                    chosen = (cx0, cy0)
                    break

            # If none of the cardinal candidates work, scan vertically below
            # the species in steps until we find a y that clears every prior
            # label rect. This guarantees no label-on-label overlap even at
            # high density — at worst the label sits a bit further from its
            # silhouette and the leader line gets longer.
            if chosen is None:
                step = max(8, lh // 4)
                # Try below first, then above, drifting outward.
                for direction in (1, -1):
                    if chosen is not None:
                        break
                    for dist in range(0, canvas_h, step):
                        ty = (sy + sh + gap + dist) if direction > 0 else (sy - lh - gap - dist)
                        tx = sx + sw // 2 - lw // 2
                        if tx < 0 or tx + lw > canvas_w:
                            continue
                        if ty < 0 or ty + lh > canvas_h:
                            continue
                        if _label_collides(tx, ty, lw, lh):
                            continue
                        # Accept even if occupancy says "full" — labels
                        # never collide, that's the priority.
                        chosen = (tx, ty)
                        break

            if chosen is None:
                # Last-resort fallback: inline below, but slot it down by
                # the height of any colliding label rect so we still avoid
                # exact label-on-label overwrite.
                inline_x = sx + sw // 2 - lw // 2
                inline_y = sy + sh + self.label_gap_px
                # Walk down until clear.
                guard = 0
                while _label_collides(inline_x, inline_y, lw, lh) and guard < 200:
                    inline_y += max(8, lh // 4)
                    guard += 1
                _draw_label_block(inline_x, inline_y, lw, sp, common_w, common_h)
                _mark(inline_x, inline_y, lw, lh)
                placed_label_rects.append((inline_x, inline_y, inline_x + lw, inline_y + lh))
                fallback_count += 1
                continue

            lx, ly = chosen
            _draw_label_block(lx, ly, lw, sp, common_w, common_h)
            _mark(lx, ly, lw, lh)
            placed_label_rects.append((lx, ly, lx + lw, ly + lh))

            # Draw leader from species-edge nearest point to label-edge nearest point.
            # Pick nearest points on each rect toward the other's center.
            lcx = lx + lw // 2
            lcy = ly + lh // 2
            # Species anchor: clamp label-center against species bbox.
            sp_ax = min(max(lcx, sx), sx + sw)
            sp_ay = min(max(lcy, sy), sy + sh)
            # Label anchor: clamp species-center against label bbox.
            lb_ax = min(max(scx, lx), lx + lw)
            lb_ay = min(max(scy, ly), ly + lh)
            # Skip leader if endpoints coincide (label overlaps species — shouldn't happen here).
            dx = lb_ax - sp_ax
            dy = lb_ay - sp_ay
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 2 and dist <= max_leader * 2:
                draw.line(
                    [(sp_ax, sp_ay), (lb_ax, lb_ay)],
                    fill=self.caption_color,
                    width=max(1, int(self.leader_line_width)),
                )
            leader_count += 1

        logger.info(
            "Leader-line labels: %d placed with leaders, %d fell back to inline.",
            leader_count, fallback_count,
        )
