"""Tests for the frame_wrap composite that wraps a poster in a Prodigi frame."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from review_app.render import frame_compositor
from review_app.render.frame_compositor import DEFAULT_FINISH, frame_wrap


def _solid(width: int, height: int, color: tuple[int, int, int] = (200, 30, 30)) -> Image.Image:
    """A vivid red rectangle so it's easy to find inside the frame photo."""
    return Image.new("RGB", (width, height), color)


def test_frame_wrap_returns_image_same_size_as_frame() -> None:
    """The output dimensions match the frame photograph dimensions."""
    out = frame_wrap(_solid(400, 600), finish=DEFAULT_FINISH)
    frame_path = frame_compositor._resolve_frame_path(DEFAULT_FINISH)
    with Image.open(frame_path) as raw:
        expected_size = raw.size
    assert out.size == expected_size


def test_frame_wrap_pastes_poster_into_inner_rect() -> None:
    """A vivid red poster shows up at the center of the framed output."""
    out = frame_wrap(_solid(400, 600, color=(220, 30, 30)), finish=DEFAULT_FINISH)
    arr = np.asarray(out).astype(np.int32)  # avoid uint8 overflow on diffs

    # Sample the dead center of the framed output — that should be inside
    # the inner-print rect since the inner rect is centered.
    cy = arr.shape[0] // 2
    cx = arr.shape[1] // 2
    r, g, b = arr[cy, cx]
    # The composited red poster should dominate the center pixel — R way
    # higher than G/B. (Exact values vary with JPEG-lossy frame backdrops.)
    assert r > g + 50, f"center R={r} G={g} B={b} — poster not visible"
    assert r > b + 50


def test_frame_wrap_falls_back_to_default_inner_rect(monkeypatch: pytest.MonkeyPatch) -> None:
    """When frame_skus.json doesn't list the finish, the default rect is used."""
    # Pretend the SKU table is empty — frame_wrap should still succeed using
    # the bundled _DEFAULT_INNER_RECT_PCT (8/8/84/84).
    monkeypatch.setattr(frame_compositor, "_load_frame_skus", lambda: [])
    out = frame_wrap(_solid(400, 600), finish=DEFAULT_FINISH)
    frame_path = frame_compositor._resolve_frame_path(DEFAULT_FINISH)
    with Image.open(frame_path) as raw:
        expected_size = raw.size
    assert out.size == expected_size


def test_frame_wrap_unknown_finish_raises() -> None:
    """An unknown finish has no frame photo on disk → FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        frame_wrap(_solid(100, 100), finish="this-finish-does-not-exist-anywhere")


def test_frame_wrap_preserves_aspect_ratio() -> None:
    """A wide poster doesn't get squished to fit a square inner rect."""
    # 800x200 = 4:1 wide poster.
    wide = _solid(800, 200, color=(20, 200, 20))
    out = frame_wrap(wide, finish=DEFAULT_FINISH)
    # Cast to int32 so we can do `g > r + 30` without uint8 overflow.
    arr = np.asarray(out).astype(np.int32)

    # Sample a row at the vertical center. Count green-dominated pixels
    # (the poster) — they should form a horizontal band, not fill the
    # whole inner rect (which would mean it got stretched).
    cy = arr.shape[0] // 2
    row = arr[cy]
    is_green = (row[:, 1] > row[:, 0] + 30) & (row[:, 1] > row[:, 2] + 30)
    green_pct = float(is_green.sum()) / float(row.shape[0])
    assert green_pct > 0.5, f"poster row not visible (green_pct={green_pct:.2%})"

    # Sample a row well above center — should have far fewer green pixels.
    near_top_y = arr.shape[0] // 4
    top_row = arr[near_top_y]
    is_green_top = (top_row[:, 1] > top_row[:, 0] + 30) & (top_row[:, 1] > top_row[:, 2] + 30)
    top_green_pct = float(is_green_top.sum()) / float(top_row.shape[0])
    assert top_green_pct < 0.2, (
        f"poster appears stretched — top row green_pct={top_green_pct:.2%}"
    )


def test_frame_wrap_default_finish_is_brown() -> None:
    """Spec lock-in: the default Prodigi finish is brown."""
    assert DEFAULT_FINISH == "brown"


def test_frame_wrap_loads_brown_frame_photo() -> None:
    """The brown classic-blank asset is on disk and openable."""
    p = frame_compositor._resolve_frame_path("brown")
    assert p.exists()
    assert p.name.startswith("classic-brown-blank.")
