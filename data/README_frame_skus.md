# `frame_skus.json` — Phase 2 frame catalog

32 SKUs (4 sizes × 8 finishes, Classic Frame Print + Mount, portrait).

## Schema (per entry)

| Field | Type | Notes |
|---|---|---|
| `internal_sku` | string | Stable, human-readable, primary key. Format: `cf-{size}-{finish-id}`. |
| `prodigi_sku` | string | Format: `GLOBAL-CFPM-{SIZE}` (size upper-cased). |
| `prodigi_attributes` | object | `{"color": "..."}` — **space-separated lowercase**, NOT hyphenated. `Antique Silver` → `"silver"`, `Antique Gold` → `"gold"`, `Dark Grey` → `"dark grey"`. |
| `size_inches` | string | `12x16`, `16x20`, `18x24`, `24x36`. |
| `size_aspect` | int[2] | Aspect ratio `[w, h]`. |
| `finish_id` | string | Hyphenated slug used in our IDs/UI: `black`, `antique-silver`, `dark-grey`, etc. |
| `finish_display` | string | UI label, e.g. `Antique Silver`. |
| `blank_asset` | string | Static URL to blank-frame composite background. |
| `chevron_asset` | string | Static URL to mitered-corner close-up — used as fallback swatch where no dedicated swatch exists yet. |
| `swatch_asset` | string | Static URL to small swatch tile for the picker. |
| `inner_rect_pct` | object | Inner print rectangle as % of blank asset W/H: `{x, y, w, h}`. |

## `inner_rect_pct` — Phase 2 approximation

The blank-frame photographs are all shot the same way (centered, square-ish bezel), so for Phase 2 we use **one approximate `inner_rect_pct` for every entry** (`x: 8, y: 8, w: 84, h: 84`). The compositor renders the poster image inside that rectangle.

**Phase 4 admin tooling will let us fine-tune per-size.** Per-finish/per-size override is just an admin-edit on the row.

## Swatch fallback

Brown, Dark Grey, and Light Grey don't have dedicated swatch tiles yet — `swatch_asset` falls back to the chevron close-up for those rows. The configurator UI doesn't care which asset it gets.

## Mapping to `prodigi_skus` table

The 32 rows here map 1:1 (by `internal_sku`) onto rows seeded by Alembic migration `0006_seed_prodigi_skus`. Pricing (`retail_price_cents`) lives **only** in the DB — never duplicated in this JSON.
