"""Build 5 transparent frame-overlay PNGs.

Output: assets/frame_overlays/{walnut,oak,black,white,pine}.png
Each PNG is RGBA at FRAME_LONG_EDGE px on the long edge, with:
  * a wooden border ~7% thick on each side
  * MITERED corners (45-degree diagonal seams) — top/bottom strips fill
    the corners with horizontal grain, left/right with vertical grain,
    each masked by a triangular polygon so the seam runs corner-to-corner
  * a fully transparent inner window
  * a subtle inner bevel (10-20px dark band) at the inside lip
  * a subtle outer bevel (light band) at the outside edge

These overlays are scaled at composite time to the actual outer frame
dimensions, so the absolute pixel size here just needs to be high enough
that scaling preserves grain detail. 4096-long is plenty.

Pine source texture is generated via Replicate Flux schnell on first run
(~$0.003) then cached at assets/frames/pine.jpg. If Replicate is
unavailable, falls back to a programmatic blonde-wood gradient.

Run from anywhere:
    python scripts/build_frame_overlays.py
"""
from __future__ import annotations

import io
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
FRAMES_DIR = ROOT / "assets" / "frames"
OVERLAYS_DIR = ROOT / "assets" / "frame_overlays"
OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)

# Output overlay dimensions. Square so it can be scaled to either
# portrait or landscape outer-frame dims by the renderer.
FRAME_LONG_EDGE = 4096
THICK_FRAC = 0.070            # frame thickness as fraction of long edge
INNER_BEVEL_FRAC = 0.012      # dark inner-lip band thickness
OUTER_BEVEL_FRAC = 0.005      # light outer-edge band thickness
INNER_BEVEL_BLUR = 6
OUTER_BEVEL_BLUR = 4

PINE_PROMPT = (
    "soft watercolor wood grain texture, raw natural pine, light blonde wood, "
    "fine grain detail, even lighting, no background, seamless tileable"
)
FLUX_MODEL = "black-forest-labs/flux-schnell"


# ----------------------- pine texture --------------------------------

def _ensure_pine_texture(target_path: Path) -> Image.Image:
    """Return an RGB pine wood texture, caching to ``target_path``.

    Tries Replicate Flux schnell first; falls back to a programmatic
    blonde-wood gradient + grain.
    """
    if target_path.exists() and target_path.stat().st_size > 50_000:
        return Image.open(target_path).convert("RGB")

    # Try Replicate.
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("REPLICATE_API_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    os.environ["REPLICATE_API_TOKEN"] = token
                    break

    if token:
        try:
            # Telemetry-wrapped Replicate proxy (Phase 0.10). Falls back to
            # the real `replicate` package when review_app isn't on the path
            # (e.g. running this script standalone in a fresh checkout).
            try:
                from review_app.ai import replicate_client as replicate  # type: ignore
            except ImportError:
                import replicate  # type: ignore
            print(f"[pine] generating via Flux schnell ...")
            t0 = time.time()
            output = replicate.run(
                FLUX_MODEL,
                input={
                    "prompt": PINE_PROMPT,
                    "aspect_ratio": "1:1",
                    "num_outputs": 1,
                    "output_format": "jpg",
                    "output_quality": 90,
                    "num_inference_steps": 4,
                },
            )
            item = output[0] if isinstance(output, list) and output else output
            if hasattr(item, "read"):
                data = item.read()
            elif isinstance(item, str):
                data = urllib.request.urlopen(item, timeout=60).read()
            else:
                raise RuntimeError(f"unexpected output type {type(item)}")
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.size != (2048, 2048):
                img = img.resize((2048, 2048), Image.LANCZOS)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(target_path, "JPEG", quality=85, optimize=True)
            elapsed = time.time() - t0
            kb = target_path.stat().st_size // 1024
            print(f"[pine] saved {target_path.name} ({kb} KB, {elapsed:.1f}s)")
            return img
        except Exception as exc:  # noqa: BLE001
            print(f"[pine] Replicate failed ({exc}); falling back to gradient")

    # Programmatic fallback: blonde pine.
    print("[pine] generating programmatic gradient")
    side = 2048
    rng = np.random.default_rng(seed=42)
    base = np.array([222, 196, 152], dtype=np.float32)  # blonde pine
    arr = np.full((side, side, 3), base, dtype=np.float32)
    # Vertical streaks (grain runs vertically, will be rotated as needed).
    streaks = rng.normal(0.0, 1.0, (side, 1)).astype(np.float32)
    streaks = np.tile(streaks, (1, side))
    # Smooth a little so streaks look like grain not noise.
    streaks_img = Image.fromarray(((streaks - streaks.min()) /
                                   max(1e-6, streaks.ptp()) * 255).astype(np.uint8))
    streaks_img = streaks_img.filter(ImageFilter.GaussianBlur(2))
    streaks = np.array(streaks_img, dtype=np.float32) / 255.0 - 0.5
    arr[..., 0] += streaks * 18.0
    arr[..., 1] += streaks * 14.0
    arr[..., 2] += streaks * 10.0
    # Per-pixel grain.
    grain = rng.normal(0.0, 4.0, (side, side, 3)).astype(np.float32)
    arr += grain
    np.clip(arr, 0.0, 255.0, out=arr)
    img = Image.fromarray(arr.astype(np.uint8), mode="RGB")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(target_path, "JPEG", quality=85, optimize=True)
    return img


# ----------------------- overlay builder -----------------------------

def _tile_texture(tex: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Tile ``tex`` to cover ``target_w x target_h`` (RGB)."""
    out = Image.new("RGB", (target_w, target_h))
    for y in range(0, target_h, tex.height):
        for x in range(0, target_w, tex.width):
            out.paste(tex, (x, y))
    return out


def _build_overlay(name: str, tex: Image.Image, side: int = FRAME_LONG_EDGE) -> Image.Image:
    """Build one transparent-window frame overlay PNG (RGBA, square).

    Mitered corners: each of top/bottom/left/right strips fills the
    full width/height including corners. We mask each strip with a
    triangular polygon at each corner so:
      * top + bottom strips occupy the corner triangles whose APEX
        points INWARD (i.e. toward the center on the horizontal axis)
      * left + right strips occupy the OTHER pair of triangles
    The diagonal seams run from each outer corner toward the inner
    corner, producing the classic 45-degree mitered look. This works
    for arbitrary thickness.

    Top strip (with grain horizontal) covers the top edge AND the
    upper-left + upper-right corner triangles whose hypotenuse is the
    diagonal from outer-corner to inner-corner.
    """
    thick = int(round(side * THICK_FRAC))
    inner = side - 2 * thick
    if inner <= 0:
        raise ValueError(f"thick {thick} too large for side {side}")

    # Tile the texture for horizontal strips (use it as-is) and rotate
    # 90 deg for vertical strips so the grain orientation flips at the
    # mitred seam (matches a real picture frame).
    horiz_tex = _tile_texture(tex, side, thick)            # full-width band
    vert_tex_src = tex.rotate(90, expand=True)
    vert_tex = _tile_texture(vert_tex_src, thick, side)    # full-height band

    # Output canvas, fully transparent.
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    # ---- Mask polygons for mitered corners. ------------------------
    # Each strip is a full rectangle; the polygon mask carves it down to
    # its share of the frame. Coordinates are in canvas space.

    # Top strip occupies y in [0, thick], full width, MINUS the corner
    # triangles that belong to left/right strips.
    # Mitered seams: diagonal from (0,0) -> (thick, thick) on the left,
    # and (side, 0) -> (side - thick, thick) on the right.
    # So the top strip's mask polygon:
    #   (0,0) -> (side,0) -> (side - thick, thick) -> (thick, thick) -> close
    top_mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(top_mask).polygon(
        [(0, 0), (side, 0), (side - thick, thick), (thick, thick)],
        fill=255,
    )

    # Bottom strip:
    #   (thick, side - thick) -> (side - thick, side - thick) ->
    #   (side, side) -> (0, side) -> close
    bot_mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(bot_mask).polygon(
        [(thick, side - thick), (side - thick, side - thick),
         (side, side), (0, side)],
        fill=255,
    )

    # Left strip:
    #   (0, 0) -> (thick, thick) -> (thick, side - thick) -> (0, side) -> close
    left_mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(left_mask).polygon(
        [(0, 0), (thick, thick), (thick, side - thick), (0, side)],
        fill=255,
    )

    # Right strip:
    #   (side, 0) -> (side, side) -> (side - thick, side - thick) ->
    #   (side - thick, thick) -> close
    right_mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(right_mask).polygon(
        [(side, 0), (side, side), (side - thick, side - thick),
         (side - thick, thick)],
        fill=255,
    )

    # ---- Paste each strip's RGB through its triangular mask. -------
    # Place horiz_tex at (0,0) for top, (0, side-thick) for bottom.
    # Place vert_tex at (0,0) for left, (side-thick, 0) for right.
    # We do this via composite: paste a full-canvas RGB image with the
    # strip in the right slot, masked by the polygon.
    def _stamp_strip(strip_img: Image.Image, paste_xy: tuple[int, int],
                     mask: Image.Image) -> None:
        full = Image.new("RGB", (side, side), (0, 0, 0))
        full.paste(strip_img, paste_xy)
        out.paste(full, (0, 0), mask)

    _stamp_strip(horiz_tex, (0, 0), top_mask)
    _stamp_strip(horiz_tex, (0, side - thick), bot_mask)
    _stamp_strip(vert_tex, (0, 0), left_mask)
    _stamp_strip(vert_tex, (side - thick, 0), right_mask)

    # ---- Inner bevel: dark band on the inside lip. ----------------
    # Draw a black rectangle at the inner edge, then erase the inner
    # transparent window — leaves a thin dark band on the FRAME side.
    bevel_w = max(2, int(round(side * INNER_BEVEL_FRAC)))
    inner_bevel = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(inner_bevel)
    bdraw.rectangle(
        [(thick - bevel_w, thick - bevel_w),
         (side - thick + bevel_w - 1, side - thick + bevel_w - 1)],
        fill=(0, 0, 0, 130),
    )
    bdraw.rectangle(
        [(thick, thick), (side - thick - 1, side - thick - 1)],
        fill=(0, 0, 0, 0),
    )
    inner_bevel = inner_bevel.filter(ImageFilter.GaussianBlur(INNER_BEVEL_BLUR))
    out = Image.alpha_composite(out, inner_bevel)

    # ---- Outer bevel: very subtle light catch on the outer edge. ---
    outer_w = max(2, int(round(side * OUTER_BEVEL_FRAC)))
    outer_bevel = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(outer_bevel)
    odraw.rectangle([(0, 0), (side - 1, side - 1)], fill=(255, 255, 255, 38))
    odraw.rectangle(
        [(outer_w, outer_w), (side - outer_w - 1, side - outer_w - 1)],
        fill=(0, 0, 0, 0),
    )
    outer_bevel = outer_bevel.filter(ImageFilter.GaussianBlur(OUTER_BEVEL_BLUR))
    out = Image.alpha_composite(out, outer_bevel)

    # ---- Force inner window fully transparent (defensive). ---------
    # The inner rectangle is already alpha=0 (no strip covered it),
    # but blur halos from bevels can creep in. Hard-clear any pixels
    # strictly inside (thick, thick) -> (side-thick-1, side-thick-1).
    arr = np.array(out)
    arr[thick:side - thick, thick:side - thick, 3] = 0
    out = Image.fromarray(arr, mode="RGBA")

    return out


# ----------------------- main -----------------------------------------

FRAMES = ("walnut", "oak", "black", "white", "pine")


def main() -> int:
    OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure pine source texture exists.
    pine_path = FRAMES_DIR / "pine.jpg"
    _ensure_pine_texture(pine_path)

    for name in FRAMES:
        src = FRAMES_DIR / f"{name}.jpg"
        if not src.exists():
            print(f"[skip] {name}: source texture {src} missing")
            continue
        tex = Image.open(src).convert("RGB")
        # Resize source to a sane working tile.
        tex_target = 2048
        if max(tex.size) > tex_target:
            ratio = tex_target / max(tex.size)
            tex = tex.resize(
                (int(tex.width * ratio), int(tex.height * ratio)),
                Image.LANCZOS,
            )
        print(f"[{name}] building overlay ...")
        t0 = time.time()
        overlay = _build_overlay(name, tex)
        out_path = OVERLAYS_DIR / f"{name}.png"
        overlay.save(out_path, "PNG", optimize=True)
        kb = out_path.stat().st_size // 1024
        elapsed = time.time() - t0
        print(f"  -> {out_path.name} ({kb} KB, {elapsed:.1f}s)")

    print("\nAll overlays built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
