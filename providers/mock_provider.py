"""Mock image provider that renders a deterministic placeholder PNG."""
from __future__ import annotations


import random
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from providers.base import BaseProvider


class MockProvider(BaseProvider):
    """A deterministic placeholder provider for local testing.

    Renders a simple fish silhouette on a white background with the
    prompt's first line at the bottom for debugging.
    """

    name = "mock"
    supports_seed = True

    def generate(
        self,
        prompt: str,
        seed: int | None = None,
        style_meta: dict | None = None,
    ) -> bytes:
        """Render a placeholder PNG image.

        Args:
            prompt: The text prompt (only the first line is rendered on image).
            seed: Optional seed for reproducible color selection.
            style_meta: Ignored by this provider; accepted for interface
                compatibility with providers that consult style metadata.

        Returns:
            PNG image bytes.
        """
        del style_meta  # unused
        if seed is not None:
            random.seed(seed)

        try:
            width_str, height_str = self.image_size.lower().split("x")
            width, height = int(width_str), int(height_str)
        except ValueError as e:
            raise ValueError(
                f"image_size must be in 'WIDTHxHEIGHT' format, got {self.image_size!r}"
            ) from e

        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        # Pick a muted random color.
        muted_color = (
            random.randint(80, 180),
            random.randint(80, 180),
            random.randint(80, 180),
        )

        # Fish body: ellipse occupying the middle of the canvas.
        body_left = int(width * 0.30)
        body_right = int(width * 0.75)
        body_top = int(height * 0.35)
        body_bottom = int(height * 0.65)
        draw.ellipse(
            [(body_left, body_top), (body_right, body_bottom)],
            fill=muted_color,
            outline="black",
            width=2,
        )

        # Tail: triangle on the left side facing left.
        tail_tip_x = int(width * 0.15)
        tail_mid_y = int(height * 0.50)
        tail_top = (body_left + 5, body_top + 5)
        tail_bottom = (body_left + 5, body_bottom - 5)
        tail_point = (tail_tip_x, tail_mid_y)
        draw.polygon(
            [tail_top, tail_bottom, tail_point],
            fill=muted_color,
            outline="black",
        )

        # Eye: small black dot on the right side of the body.
        eye_x = int(width * 0.68)
        eye_y = int(height * 0.45)
        eye_r = max(2, int(min(width, height) * 0.01))
        draw.ellipse(
            [(eye_x - eye_r, eye_y - eye_r), (eye_x + eye_r, eye_y + eye_r)],
            fill="black",
        )

        # Prompt first line at bottom for debugging.
        first_line = prompt.splitlines()[0] if prompt else ""
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        text_y = height - 20
        draw.text((10, text_y), first_line, fill="black", font=font)

        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
