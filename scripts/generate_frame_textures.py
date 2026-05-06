"""Generate 4 frame textures via Replicate Flux.

Outputs to assets/frames/{walnut,oak,black,white}.jpg at ~4096x4096,
JPEG q85, target ~500KB each.

Run once. Idempotent — skips frames that already exist.
"""
from __future__ import annotations

import io
import os
import sys
import time
import urllib.request
from pathlib import Path

try:
    # Telemetry-wrapped Replicate proxy (Phase 0.10). Drop-in for
    # `import replicate` — pure passthrough when AI_LOGGING_ENABLED is unset.
    # Falls back to the real replicate package if review_app isn't on the
    # path (this script may be run standalone before the package is
    # installed in dev environments).
    try:
        from review_app.ai import replicate_client as replicate
    except ImportError:
        import replicate
except ImportError:
    print("pip install replicate", file=sys.stderr)
    sys.exit(1)

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "assets" / "frames"
OUT.mkdir(parents=True, exist_ok=True)

# Use Flux schnell for speed/cost (~$0.003 per image on Replicate)
MODEL = "black-forest-labs/flux-schnell"

FRAMES = [
    (
        "walnut",
        "seamless tileable photographic texture of polished walnut wood, "
        "warm dark chocolate brown, fine even grain detail, museum frame quality, "
        "soft even studio lighting, no shadows, no edges, full frame texture only",
    ),
    (
        "oak",
        "seamless tileable photographic texture of light blonde oak wood, "
        "honey-golden brown, visible straight grain, museum frame quality, "
        "soft even studio lighting, no shadows, no edges, full frame texture only",
    ),
    (
        "black",
        "seamless tileable photographic texture of matte black painted wood, "
        "subtle wood grain showing through, deep charcoal black, "
        "soft even studio lighting, no shadows, no edges, full frame texture only",
    ),
    (
        "white",
        "seamless tileable photographic texture of matte white-washed wood, "
        "cream off-white, subtle wood grain visible, gallery frame quality, "
        "soft even studio lighting, no shadows, no edges, full frame texture only",
    ),
]


def main() -> int:
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        # Try .env
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("REPLICATE_API_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    os.environ["REPLICATE_API_TOKEN"] = token
                    break
    if not token:
        print("REPLICATE_API_TOKEN not set", file=sys.stderr)
        return 1

    for name, prompt in FRAMES:
        out_path = OUT / f"{name}.jpg"
        if out_path.exists() and out_path.stat().st_size > 100_000:
            print(f"  [skip] {name}.jpg exists ({out_path.stat().st_size // 1024} KB)")
            continue

        print(f"  [{name}] generating ...")
        t0 = time.time()
        # Flux schnell is fast — 1024x1024 max output, then we upscale by tiling
        attempt = 0
        while True:
            try:
                output = replicate.run(
                    MODEL,
                    input={
                        "prompt": prompt,
                        "aspect_ratio": "1:1",
                        "num_outputs": 1,
                        "output_format": "jpg",
                        "output_quality": 90,
                        "num_inference_steps": 4,
                    },
                )
                break
            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "throttled" in msg.lower():
                    attempt += 1
                    if attempt > 6:
                        raise
                    wait = 12
                    print(f"    rate limited, sleeping {wait}s ...")
                    time.sleep(wait)
                    continue
                raise

        # output is a list of FileOutput objects (or URLs)
        if isinstance(output, list) and output:
            item = output[0]
        else:
            item = output

        if hasattr(item, "read"):
            data = item.read()
        elif isinstance(item, str):
            data = urllib.request.urlopen(item, timeout=60).read()
        else:
            print(f"  FAIL {name}: unexpected output type {type(item)}", file=sys.stderr)
            continue

        # Open, resize, save as JPEG q85
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # 2048x2048 is plenty: at 200px frame thickness, this tiles 10x with
        # Lanczos resampling that smooths any seams.
        if img.size != (2048, 2048):
            img = img.resize((2048, 2048), Image.LANCZOS)
        img.save(out_path, "JPEG", quality=85, optimize=True)
        size_kb = out_path.stat().st_size // 1024
        elapsed = time.time() - t0
        print(f"    {name}.jpg saved ({size_kb} KB, {elapsed:.1f}s)")

    print("\nAll frame textures generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
