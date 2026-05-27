"""Build a walnut frame overlay sized exactly for the customizer canvas.

Uses the existing 9-slice logic in poster_layout.renderer so the mitered
corners stay clean at the portrait aspect — same code path the production
renderer uses.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, "/Users/grant/claude-workspace/Wildlife/wildprint")

from poster_layout.renderer import _load_frame_overlay, _build_frame_from_overlay

CANVAS_W, CANVAS_H = 5400, 7200
# Frame thickness in pixels — matches reference's slim walnut frame proportions
# (reference frame is ~2.5% of poster long side). Long side here is 7200 → 180px.
FRAME_THICK = 180

src = _load_frame_overlay("walnut")
if src is None:
    sys.exit("walnut overlay missing from assets/frame_overlays/")
frame = _build_frame_from_overlay(src, CANVAS_W, CANVAS_H, FRAME_THICK)
out = Path("/Users/grant/claude-workspace/Wildlife/wildprint/output/customizer/frame_walnut.png")
frame.save(out, "PNG")
print(f"wrote {out} ({frame.size[0]}x{frame.size[1]}, thickness={FRAME_THICK}px)")
