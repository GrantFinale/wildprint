"""Generate and upscale landscape backgrounds via Replicate API."""
from __future__ import annotations

import logging
import uuid
import time
from pathlib import Path

import replicate
import requests

from config.settings import PROJECT_ROOT, REPLICATE_API_TOKEN

logger = logging.getLogger(__name__)

# Output directory for generated backgrounds
BACKGROUNDS_DIR = Path(PROJECT_ROOT) / "output" / "backgrounds"

# Flux Pro Ultra: 2752x2752 max (7.6 MP native). Excellent landscapes.
_FLUX_MODEL = "black-forest-labs/flux-1.1-pro-ultra"
# Real-ESRGAN 4x upscaler (produces 11008x11008 from 2752x2752 = 121 MP)
_ESRGAN_MODEL = "nightmareai/real-esrgan"


def generate_landscape(prompt: str, aspect_ratio: str = "3:2") -> Path:
    """Generate a landscape with Flux 1.1 Pro Ultra and upscale 4x.

    Args:
        prompt: Natural-language landscape description.
        aspect_ratio: One of "1:1", "16:9", "3:2", "2:3", "4:5", "5:4", "9:16"
                      for Flux Pro Ultra.

    Returns:
        Path to the final upscaled PNG, written to output/backgrounds/.
    """
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN not set in .env")

    BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

    client = replicate.Client(api_token=REPLICATE_API_TOKEN)

    # --- 1. Generate via Flux Pro Ultra at max native resolution ---
    logger.info("Generating landscape via Flux Pro Ultra: %s", prompt[:80])
    t0 = time.time()
    output = client.run(
        _FLUX_MODEL,
        input={
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "raw": False,
            "safety_tolerance": 2,
        },
    )

    raw_path = BACKGROUNDS_DIR / f"flux_{uuid.uuid4().hex[:8]}.png"
    _save_replicate_output(output, raw_path)
    logger.info("Flux generated in %.1fs -> %s", time.time() - t0, raw_path.name)

    # --- 2. Upscale 4x via Real-ESRGAN ---
    # ESRGAN has a GPU memory cap (~2.1M pixels input). Downscale input if needed.
    logger.info("Upscaling with Real-ESRGAN 4x...")
    t1 = time.time()
    from PIL import Image as _PILImage
    import io as _io

    _MAX_ESRGAN_PIXELS = 2_000_000
    with _PILImage.open(raw_path) as _img:
        _img = _img.convert("RGB")
        _w, _h = _img.size
        if _w * _h > _MAX_ESRGAN_PIXELS:
            _ratio = (_MAX_ESRGAN_PIXELS / (_w * _h)) ** 0.5
            _new = (int(_w * _ratio), int(_h * _ratio))
            logger.info("Resizing %dx%d -> %dx%d for ESRGAN", _w, _h, *_new)
            _img = _img.resize(_new, _PILImage.LANCZOS)
        _buf = _io.BytesIO()
        _img.save(_buf, format="PNG")
        _buf.seek(0)
        upscaled = client.run(
            _ESRGAN_MODEL,
            input={
                "image": _buf,
                "scale": 4,
                "face_enhance": False,
            },
        )

    final_path = BACKGROUNDS_DIR / f"bg_{uuid.uuid4().hex[:8]}.png"
    _save_replicate_output(upscaled, final_path)
    logger.info("Upscaled in %.1fs -> %s", time.time() - t1, final_path.name)

    return final_path


def _save_replicate_output(output, dest: Path) -> None:
    """Save a Replicate output (FileOutput object or URL string) to disk."""
    if hasattr(output, "read"):
        dest.write_bytes(output.read())
        return
    if isinstance(output, list) and output:
        output = output[0]
    if hasattr(output, "read"):
        dest.write_bytes(output.read())
        return
    if isinstance(output, str):
        resp = requests.get(output, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return
    raise RuntimeError(f"Unexpected Replicate output type: {type(output)}")


# Habitat preset prompts
PRESET_LANDSCAPES: dict[str, str] = {
    "alpine_lake": (
        "Rocky Mountain alpine lake at dawn, mirror-calm water reflecting "
        "snow-capped peaks, pine forest edges, golden morning light, "
        "photorealistic, hyper-detailed, 8k landscape photography"
    ),
    "boreal_forest": (
        "Northern boreal forest lake, tall spruce and fir trees, misty "
        "morning, loon calling across still water, muted blue-green palette, "
        "photorealistic landscape, rich depth of field"
    ),
    "prairie_pothole": (
        "Great Plains prairie pothole wetland, tall grasses, scattered "
        "ponds reflecting blue sky, summer evening light, wide-open horizon, "
        "photorealistic, subtle color grading"
    ),
    "southern_swamp": (
        "Louisiana bayou at golden hour, bald cypress trees with hanging "
        "Spanish moss, still tea-colored water, warm green and gold tones, "
        "atmospheric mist, photorealistic, cinematic"
    ),
    "everglades": (
        "Florida Everglades sawgrass marsh at sunrise, endless wetland, "
        "scattered mangrove islands, pastel pink and blue sky, photorealistic, "
        "tropical feel"
    ),
    "pacific_stream": (
        "Pacific Northwest old-growth rainforest stream, ferns, moss-covered "
        "boulders, salmon water, dappled green light filtering through "
        "Douglas fir canopy, photorealistic, cool moody palette"
    ),
    "desert_spring": (
        "Southwestern desert oasis, palm trees around a turquoise spring, "
        "red rock canyon walls, clear blue sky, crisp afternoon light, "
        "photorealistic, warm earth tones"
    ),
    "coastal_marsh": (
        "Atlantic coastal salt marsh at dawn, winding tidal creeks, green "
        "spartina grass, low mist, soft pink sunrise, photorealistic, "
        "quiet atmospheric mood"
    ),
    "rocky_shoreline": (
        "Great Lakes rocky shoreline, clear blue water meeting granite "
        "boulders, pine trees, cumulus clouds, photorealistic, bright "
        "summer afternoon"
    ),
    "bass_pond": (
        "Shallow weedy southern bass pond, lily pads and cypress knees, "
        "warm green water, sun-dappled, photorealistic, peaceful summer "
        "afternoon"
    ),
}
