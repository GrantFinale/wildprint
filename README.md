# wildprint

A structured pipeline for generating multi-style wildlife illustrations and selecting masters for poster art.

> **Consumer brand:** the customer-facing storefront ships under the **FishingPoster.com** brand (capital F + P). `wildprint` is the internal repo, package, and CLI name; it never appears in user-visible copy.

## What it does

- Generates multiple variations of each species in multiple illustration styles (scientific, watercolor, vintage engraving, ...) from a single source of truth.
- Keeps composition consistent across species and styles so they can share a poster canvas.
- Provides a reviewable workflow: every generation is logged, normalized, and inspected through a small Flask app.
- Produces a clean `output/master/{style}/{species}.png` tree ready to feed into a future poster generator.

## Project structure

```
wildprint/
  config/             # settings + env loading
  data/
    species/          # species.json, species.csv (catalog)
    styles/           # styles.json (style catalog)
  prompts/
    base_prompt.txt
    styles/<slug>.txt
  providers/          # mock + openai image providers
  scripts/            # batch_generate, normalize_images, select_master, build_manifest
  review_app/         # Flask review/selection UI
  poster_layout/      # placeholder interfaces for the future poster engine
  output/
    raw/<style>/<species>/
    normalized/<style>/<species>/
    master/<style>/<species>.png
  metadata/
    manifest.json
    generation.log
  requirements.txt
  .env.example
```

## Prerequisites

- Python 3.11+
- (Optional) An OpenAI API key, only if you want to run the `openai` provider. The default `mock` provider needs nothing.

## Install

```bash
cd wildprint
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Configure

Edit `.env`:

- `OPENAI_API_KEY` — required only when `DEFAULT_PROVIDER=openai`.
- `DEFAULT_PROVIDER` — `mock` (offline, deterministic) or `openai`.
- `DEFAULT_MODEL` — image model name passed to the provider, e.g. `gpt-image-1`.
- `DEFAULT_IMAGE_SIZE` — `WxH`, e.g. `1536x1024`. OpenAI accepts `1024x1024`, `1536x1024`, `1024x1536`.
- `DEFAULT_NUM_VARIATIONS` — variations per `(species, style)` combo.
- `DEFAULT_SEED` — optional deterministic seed (mock only — see Reproducibility).
- `CANVAS_WIDTH` / `CANVAS_HEIGHT` — normalized canvas size.
- `FLASK_SECRET_KEY` — secret for the review app. Replace in any shared environment.

## Editing species

Species live in `data/species/species.json` (canonical) and `data/species/species.csv` (mirror, easy to edit in a spreadsheet).

Each record requires:

- `slug` — stable id (e.g. `smallmouth_bass`).
- `common_name`, `scientific_name`.
- `category` — e.g. `fish`.
- `key_visual_traits` — short anatomically specific descriptors that the prompt template injects (e.g. `"olive-bronze flanks, vertical bars, red eye"`). This is the difference between a generic fish and a recognizable smallmouth — be specific.
- `relative_scale_index` — ratio of typical adult body length to the Smallmouth Bass baseline (1.0). Northern Pike ~2.5, Bluegill ~0.6, etc.
- `habitat_tags` — free-form list, used in metadata.
- `enabled` — set `false` to skip a species without deleting it.

To add a species: append a record to `species.json` (and mirror it in `species.csv`), then rerun batch generation. To remove one, set `enabled: false`.

## Editing styles

Styles live in `data/styles/styles.json` and are first-class data, not hardcoded branches.

Each style has a `slug`, `style_name`, `description`, and a per-style prompt fragment. Adding a new style is:

1. Add an entry to `data/styles/styles.json`.
2. Optionally add a sibling `prompts/styles/<slug>.txt` for clarity.
3. Rerun batch generation.

No code changes required. Style flows automatically through prompts, metadata, output paths, manifest, review app, and poster layout.

## Running batch generation

```bash
# Generate everything enabled, mock provider (no API key needed):
python -m scripts.batch_generate

# One species, all styles:
python -m scripts.batch_generate --species smallmouth_bass

# One style, all species:
python -m scripts.batch_generate --style watercolor

# One species, one style, 5 variations, with a seed:
python -m scripts.batch_generate --species northern_pike --style scientific --variations 5 --seed 42

# Use the real OpenAI provider:
python -m scripts.batch_generate --provider openai --model gpt-image-1

# Dry run to preview prompts:
python -m scripts.batch_generate --dry-run
```

## Normalizing images

After generation, crop, center, and standardize each image onto the configured canvas:

```bash
python -m scripts.normalize_images
python -m scripts.normalize_images --species smallmouth_bass --style scientific
```

## Reviewing and selecting masters

Run the Flask review app:

```bash
python -m review_app.app
# open http://127.0.0.1:5000
```

Click through style → species → pick one master per `(species, style)`. After reviewing, hit "Copy Masters" in the UI or run:

```bash
python -m scripts.select_master --copy
```

Master images land in `output/master/{style_slug}/{species_slug}.png` — this is the directory the future poster engine consumes.

## Rebuilding the manifest

If `metadata/manifest.json` gets out of sync with what's on disk:

```bash
python -m scripts.build_manifest
```

## Reproducibility

Seeds are threaded through config, the CLI, and per-image metadata. The `mock` provider is fully deterministic given a seed. The OpenAI image models do **not** currently support seeds, so the seed is still recorded in metadata for traceability but is **not** honored by the API. Document this expectation with collaborators so no one is surprised by run-to-run variation on the OpenAI provider.

## Extending to other categories (turtles, birds, amphibians, plants)

Add records to `species.json` / `species.csv` with a new `category` value. A few notes:

- `key_visual_traits` should remain anatomically specific — don't fall back to "a bird".
- `relative_scale_index` should remain comparable across categories. Tune against the Smallmouth Bass 1.0 baseline: a 20 cm turtle ≈ 0.5, a 1 m heron ≈ 2.0.
- Style handling is category-agnostic, so the same three styles work out of the box.
- If a category needs a different base prompt (e.g., birds standing rather than the default left-facing fish profile), add a `category_overrides` block later. For now, the simplest path is cloning `prompts/base_prompt.txt` per category.

## Style as a first-class entity

Style is data, not a code branch. It flows through prompts, metadata, output paths, the manifest, the review app, and the future poster layout. Adding a new style does not require touching any conditional logic — you add a record and rerun.

## Poster layout (placeholder)

`poster_layout/` is a scaffold defining the interfaces the future poster renderer will implement. It consumes master image paths, species metadata, style metadata, and `relative_scale_index` data. No rendering is implemented yet — see `poster_layout/interfaces.py` for the contract.

## Provider abstraction

`providers/base.py` defines the provider interface. `mock_provider.py` gives a fully offline, deterministic flow for development. `openai_provider.py` wraps the OpenAI Images SDK. To add a new provider, subclass `BaseProvider` and register it in `providers/__init__.py`.

## Directory outputs

- `output/raw/{style}/{species}/{species}_{style}_v{n}.png` — generator output plus a sidecar JSON of prompt + parameters.
- `output/normalized/{style}/{species}/...` — cropped, centered, canvas-standardized.
- `output/master/{style}/{species}.png` — the chosen master per `(species, style)`.
- `metadata/manifest.json` — single source of truth for all generations.
- `metadata/generation.log` — append-only log.

## Next steps

- Implement `poster_layout.LayoutEngine` and `PosterRenderer` for real.
- Add category-specific base prompts (birds, turtles, plants).
- Add batch QA metrics (detect off-white background, detect orientation drift).
- Add a web upload flow in the review app for manual replacements.

