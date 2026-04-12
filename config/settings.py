"""Project settings for wildprint.

Loads configuration from environment variables with sensible defaults.
If `python-dotenv` is installed, a `.env` file at the project root is
loaded automatically.
"""
from __future__ import annotations


from __future__ import annotations

import os
from pathlib import Path

# Optional .env loading --------------------------------------------------------
try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # noqa: BLE001 - dotenv is optional
    pass


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# Paths ------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(
    os.getenv("WILDPRINT_PROJECT_ROOT", str(Path(__file__).resolve().parent.parent))
)

DATA_DIR: Path = Path(os.getenv("WILDPRINT_DATA_DIR", str(PROJECT_ROOT / "data")))
SPECIES_DIR: Path = Path(
    os.getenv("WILDPRINT_SPECIES_DIR", str(DATA_DIR / "species"))
)
STYLES_DIR: Path = Path(os.getenv("WILDPRINT_STYLES_DIR", str(DATA_DIR / "styles")))

PROMPTS_DIR: Path = Path(
    os.getenv("WILDPRINT_PROMPTS_DIR", str(PROJECT_ROOT / "prompts"))
)
PROMPTS_STYLES_DIR: Path = Path(
    os.getenv("WILDPRINT_PROMPTS_STYLES_DIR", str(PROMPTS_DIR / "styles"))
)

OUTPUT_DIR: Path = Path(
    os.getenv("WILDPRINT_OUTPUT_DIR", str(PROJECT_ROOT / "output"))
)
RAW_DIR: Path = Path(os.getenv("WILDPRINT_RAW_DIR", str(OUTPUT_DIR / "raw")))
NORMALIZED_DIR: Path = Path(
    os.getenv("WILDPRINT_NORMALIZED_DIR", str(OUTPUT_DIR / "normalized"))
)
MASTER_DIR: Path = Path(
    os.getenv("WILDPRINT_MASTER_DIR", str(OUTPUT_DIR / "master"))
)
METADATA_DIR: Path = Path(
    os.getenv("WILDPRINT_METADATA_DIR", str(PROJECT_ROOT / "metadata"))
)

# Derived file paths -----------------------------------------------------------
SPECIES_JSON: Path = SPECIES_DIR / "species.json"
SPECIES_CSV: Path = SPECIES_DIR / "species.csv"
STYLES_JSON: Path = STYLES_DIR / "styles.json"
MANIFEST_PATH: Path = METADATA_DIR / "manifest.json"
BASE_PROMPT_PATH: Path = PROMPTS_DIR / "base_prompt.txt"
BASE_PROMPT_BY_CATEGORY: dict[str, Path] = {
    "fish": BASE_PROMPT_PATH,
    "turtle": PROMPTS_DIR / "base_prompt_turtle.txt",
    "bird": PROMPTS_DIR / "base_prompt_bird.txt",
    "plant": PROMPTS_DIR / "base_prompt_plant.txt",
}

# Provider / model settings ----------------------------------------------------
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
RECRAFT_API_KEY: str | None = os.getenv("RECRAFT_API_KEY")
DEFAULT_PROVIDER: str = os.getenv("WILDPRINT_DEFAULT_PROVIDER", "mock")
DEFAULT_MODEL: str = os.getenv("WILDPRINT_DEFAULT_MODEL", "gpt-image-1")
DEFAULT_IMAGE_SIZE: str = os.getenv("WILDPRINT_DEFAULT_IMAGE_SIZE", "1536x1024")

DEFAULT_NUM_VARIATIONS: int = _get_int("WILDPRINT_DEFAULT_NUM_VARIATIONS", 3)
DEFAULT_SEED: int | None = _get_optional_int("WILDPRINT_DEFAULT_SEED")

# Canvas / image processing ----------------------------------------------------
CANVAS_WIDTH: int = _get_int("WILDPRINT_CANVAS_WIDTH", 2048)
CANVAS_HEIGHT: int = _get_int("WILDPRINT_CANVAS_HEIGHT", 1365)
WHITE_BG_THRESHOLD: int = _get_int("WILDPRINT_WHITE_BG_THRESHOLD", 245)
