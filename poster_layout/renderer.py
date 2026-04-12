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

from PIL import Image, ImageDraw, ImageFont

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


def _draw_species_label(
    draw: ImageDraw.ImageDraw,
    placed: PlacedItem,
    common_font: ImageFont.ImageFont,
    scientific_font: ImageFont.ImageFont,
    common_color: str,
    sci_color: str,
    label_gap_px: int,
    common_letter_spacing: int = 4,
) -> None:
    """Draw a centered two-line label below a placed item.

    Line 1 is the common name in tracked small caps; line 2 is the
    scientific name in italic. The label is anchored ``label_gap_px``
    below the placed item's bottom edge and centered on its horizontal
    midpoint.
    """
    sp = placed.species_ref
    cx_canvas = placed.x + placed.draw_width // 2
    label_top = placed.y + placed.draw_height + label_gap_px

    common = (sp.common_name or "").upper()
    if common:
        # Measure with tracking applied so we can center it.
        advances: list[int] = []
        for ch in common:
            cw, _ = _text_size(draw, ch, common_font)
            advances.append(cw)
        common_w = sum(advances) + common_letter_spacing * max(0, len(common) - 1)
        _, common_h = _text_size(draw, common, common_font)
        x_cursor = cx_canvas - common_w // 2
        for ch, adv in zip(common, advances):
            draw.text((x_cursor, label_top), ch, font=common_font, fill=common_color)
            x_cursor += adv + common_letter_spacing
        next_top = label_top + common_h + max(6, common_h // 4)
    else:
        next_top = label_top

    sci = sp.scientific_name or ""
    if sci:
        sci_w, _sci_h = _text_size(draw, sci, scientific_font)
        sci_x = cx_canvas - sci_w // 2
        draw.text((sci_x, next_top), sci, font=scientific_font, fill=sci_color)


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

    def render(self, result: LayoutResult, output_path: Path) -> None:
        spec = result.poster

        bg = spec.background_color or self.DEFAULT_BACKGROUND
        title_c, sci_c, rule_c, caption_c = _adaptive_palette(bg)
        self.title_color = title_c
        self.scientific_color = sci_c
        self.rule_color = rule_c
        self.caption_color = caption_c
        logger.info(
            "editorial-multi background: %s (luminance=%.0f, title ink=%s)",
            bg,
            _relative_luminance(bg),
            title_c,
        )

        canvas = Image.new(
            "RGB", (spec.canvas_width, spec.canvas_height), color=bg
        )
        draw = ImageDraw.Draw(canvas)

        if not result.placements:
            logger.warning(
                "EditorialMultiRenderer got zero placements; rendering "
                "background-only poster."
            )
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path, format="PNG")
            return

        canvas_w = spec.canvas_width
        canvas_h = spec.canvas_height

        # Match SmallEnsembleLayoutEngine's default fractions so the title
        # and caption bands frame the species area cleanly.
        title_band_h = int(round(canvas_h * 0.16))
        caption_band_h = int(round(canvas_h * 0.10))

        # Load fonts (lazy).
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
            bucket = placed.y // 80 * 80
            is_dense = len(shelves.get(bucket, [])) > 4
            _draw_species_label(
                draw=draw,
                placed=placed,
                common_font=dense_common if is_dense else label_common_regular,
                scientific_font=dense_italic if is_dense else label_italic_font,
                common_color=self.title_color,
                sci_color=self.scientific_color,
                label_gap_px=self.label_gap_px,
                common_letter_spacing=dense_spacing if is_dense else self.label_letter_spacing,
            )

        # 2b. Border plants — decorative waterline strip above caption band.
        if self.border_plants:
            self._draw_border_plants(canvas, spec, caption_band_h)

        # 3. Caption band. The subtitle is already used in the title block,
        # so populate the caption from habitat tags (or fall back to the
        # subtitle when no habitats are available) so the band isn't empty.
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

        # 4. Optional logo — composited centered in the lower caption band.
        # Supports PNG (alpha) or JPEG. Scaled to fit ~30% of canvas width
        # and ~40% of caption band height. For bulk-purchase buyers to place
        # their brand centered at the bottom of the poster.
        if hasattr(self, "_logo_path") and self._logo_path:
            try:
                with Image.open(self._logo_path) as logo_src:
                    logo = logo_src.convert("RGBA")
                    max_logo_w = int(canvas_w * 0.30)
                    max_logo_h = int(caption_band_h * 0.40)
                    logo_scale = min(
                        max_logo_w / max(1, logo.width),
                        max_logo_h / max(1, logo.height),
                    )
                    logo_w = max(1, int(logo.width * logo_scale))
                    logo_h = max(1, int(logo.height * logo_scale))
                    logo_resized = logo.resize((logo_w, logo_h), Image.LANCZOS)
                    logo_x = (canvas_w - logo_w) // 2
                    logo_y = canvas_h - int(caption_band_h * 0.55)
                    canvas.paste(
                        logo_resized.convert("RGB"),
                        (logo_x, logo_y),
                        mask=logo_resized.split()[3],
                    )
                    logger.info(
                        "Logo composited from %s (%dx%d at y=%d)",
                        self._logo_path, logo_w, logo_h, logo_y,
                    )
            except (OSError, Image.UnidentifiedImageError) as exc:
                logger.warning("Could not load logo %s: %s", self._logo_path, exc)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG")

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
            resized = src.resize(
                (max(1, placed.draw_width), max(1, placed.draw_height)),
                resample=Image.Resampling.LANCZOS,
            )
        alpha = resized.getchannel("A")
        if alpha.getextrema()[0] < 255:
            canvas.paste(resized, (placed.x, placed.y), mask=alpha)
        else:
            canvas.paste(resized.convert("RGB"), (placed.x, placed.y))
