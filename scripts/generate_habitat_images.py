"""Generate habitat-illustration images for the slider previews on /create.

Output: assets/habitat/{dimension}_{value}.png — one image per habitat state.
Style: clean watercolor field-guide cross-section, matching the wildprint
aesthetic, on a soft ivory paper backdrop.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from providers import get_provider  # noqa: E402

OUT = ROOT / "assets" / "habitat"
OUT.mkdir(parents=True, exist_ok=True)

STYLE_SUFFIX = (
    " — soft watercolor illustration, gentle pigment bleeding, "
    "muted natural tones, cross-section view from a low side angle, "
    "clean ivory paper background (#FFFFFF), no text, no labels, "
    "isolated illustrative subject, painterly edges, editorial field-guide aesthetic"
)

PROMPTS = {
    # Water type — 6 states
    "water_type_lake": "An open freshwater lake with a calm shoreline, distant tree-lined edge, blue-green water reflecting the sky" + STYLE_SUFFIX,
    "water_type_pond": "A small backyard pond surrounded by reeds and tall grass, lily pads on the surface, sunlight on still water" + STYLE_SUFFIX,
    "water_type_river": "A meandering freshwater river with gentle current, smooth banks, reeds along the edge, soft afternoon light" + STYLE_SUFFIX,
    "water_type_stream": "A narrow rocky woodland stream with clear water tumbling over moss-covered stones, ferns on the bank" + STYLE_SUFFIX,
    "water_type_reservoir": "A man-made reservoir with a low concrete dam wall in the distance, broad calm water, evergreen forest beyond" + STYLE_SUFFIX,
    "water_type_marsh": "A cattail marsh with shallow water visible between dense stands of reeds, soft dawn light" + STYLE_SUFFIX,

    # Depth — 3 states
    "depth_shallow": "A shallow lake edge cross-section showing the sandy bottom clearly visible just below the water surface, no more than 4 feet deep, sunlight reaching the bed" + STYLE_SUFFIX,
    "depth_moderate": "A moderately deep lake cross-section showing graduated water column, surface light fading to dim middle depths around 10 feet, faint bottom" + STYLE_SUFFIX,
    "depth_deep": "A deep lake cross-section showing dark blue water descending into shadow, the bottom invisible, light only reaching the upper third" + STYLE_SUFFIX,

    # Flow — 4 states
    "flow_still": "A glassy mirror-still lake surface with a perfect reflection of trees, no ripples, total calm" + STYLE_SUFFIX,
    "flow_slow": "A gently flowing river surface with subtle drifting current, soft swirling lines, slow lazy water" + STYLE_SUFFIX,
    "flow_moderate": "A moderately flowing river with visible current lines, light ripples and small wave crests, steady downstream movement" + STYLE_SUFFIX,
    "flow_fast": "A fast-flowing rocky river with white water rapids, splashing foam over stones, energetic turbulent current" + STYLE_SUFFIX,

    # Vegetation — 4 states
    "vegetation_heavy": "A pond surface densely covered in lily pads, cattails along the edge, thick aquatic weeds visible just below the surface" + STYLE_SUFFIX,
    "vegetation_moderate": "A pond with a moderate scattering of lily pads, occasional patches of submerged weeds, partial open water" + STYLE_SUFFIX,
    "vegetation_sparse": "A mostly open pond with only a few lily pads and a thin band of reeds along one edge, mostly clear water" + STYLE_SUFFIX,
    "vegetation_none": "A perfectly clean lake surface with no aquatic plants, no weeds, no lily pads, sandy bottom visible at the shoreline" + STYLE_SUFFIX,

    # Clarity — 3 states
    "clarity_clear": "Crystal clear freshwater showing perfect transparency, the bottom rocks and individual pebbles visible in sharp detail through the water" + STYLE_SUFFIX,
    "clarity_moderate": "Moderately clear lake water with a slight blue-green tint, the bottom visible in shallow areas but fading into haze deeper down" + STYLE_SUFFIX,
    "clarity_murky": "Murky tea-colored stained freshwater with low visibility, suspended sediment and tannin tint, the bottom invisible just below the surface" + STYLE_SUFFIX,
}


def main() -> int:
    provider_cls = get_provider("openai")
    provider = provider_cls(model="gpt-image-1", image_size="1024x1024", quality="medium")

    total = len(PROMPTS)
    done = 0
    failed = 0
    for slug, prompt in PROMPTS.items():
        out_path = OUT / f"{slug}.png"
        if out_path.exists() and out_path.stat().st_size > 50_000:
            print(f"  skip  {slug}.png (already exists)")
            done += 1
            continue
        t0 = time.time()
        try:
            png_bytes = provider.generate(prompt)
            out_path.write_bytes(png_bytes)
            print(f"  ok    {slug}.png ({len(png_bytes)//1024} KB, {time.time()-t0:.1f}s)")
            done += 1
        except Exception as e:
            print(f"  FAIL  {slug}: {e}")
            failed += 1
            time.sleep(1)

    print(f"\n{done}/{total} done, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
