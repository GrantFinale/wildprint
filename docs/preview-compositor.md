# Frame preview compositor (Phase 2)

> Client-side canvas compositor that paints a tier-2 watermarked poster into a frame mockup at sub-100ms response on every dropdown / swatch change.

## TL;DR

```
[server]                                             [browser]
render_spec  ──tier-2 render──► JPEG @ Spaces ─URL──► <img>
                                                       │
                                            ┌──────────┘
                                            ▼
frame_skus.json ─────► configurator.js ─► <canvas> = blank PNG + poster
                                                       ▲
                                            user picks ┘ size / finish
                                            (zero network, ~1-3 ms repaint)
```

Three artifacts ship together:

1. **`/data/frame_skus.json`** — 32 SKUs (4 sizes × 8 finishes), inner-rect bounds, and asset URLs.
2. **`review_app/static/js/preview/configurator.js`** — the canvas compositor.
3. **`review_app/templates/preview/configurator.html`** — the page with size dropdown + finish picker.

Wired together by the `/preview` blueprint in `review_app/preview/`.

## Why client-side compositing

The brief mandates **sub-100ms response on every dropdown change**. A round-trip-per-change architecture cannot hit that target — even with a warm cache, network alone burns 50-300 ms on consumer connections.

So the design is: server-side renders **one watermarked tier-2 preview JPEG** per `render_spec` (cached). The browser downloads it once, downloads all 8 frame blanks once, and from then on every size/finish change is a single `ctx.drawImage()` pair on a `<canvas>` — under 5 ms in practice.

## Three-tier render pipeline

| Tier | Long edge | Watermarked? | Where the URL comes from | Used by |
|---|---|---|---|---|
| 1. Thumbnail | 400 px | no (size protects it) | `render_outputs` table | Browse grid |
| 2. **Preview** | **2400 px** | **yes** (subtle diagonal) | `render_outputs` table | **This compositor** |
| 3. Print | 7200×10800 | no | private bucket, signed URL | Prodigi only, post-payment |

Phase 2 backend (sibling agent) owns tier-2 rendering + caching. Phase 2 frontend (this work) only consumes the URL.

## Data shape: `frame_skus.json`

```jsonc
{
  "internal_sku":        "cf-16x20-brown",
  "prodigi_sku":         "GLOBAL-CFPM-16X20",
  "prodigi_attributes":  {"color": "brown"},      // space-separated, NOT hyphenated
  "size_inches":         "16x20",
  "size_aspect":         [4, 5],
  "finish_id":           "brown",                  // hyphenated for our IDs
  "finish_display":      "Brown",                  // marketing name
  "blank_asset":         "/static/frames/classic-brown-blank.jpg",
  "chevron_asset":       "/static/frames/classic-brown-chevron.jpg",
  "swatch_asset":        "/static/frames/classic-brown-chevron.jpg",
  "inner_rect_pct":      {"x": 8, "y": 8, "w": 84, "h": 84}
}
```

`inner_rect_pct` describes the inner-print rectangle as **percentages of the blank asset's W/H**, so the JS doesn't care whether the blank is 1600×2000 or 600×800.

### Phase 2 approximation

All 32 entries currently use the same `inner_rect_pct` per finish (in fact, the same one for all 32 entries: `{x: 8, y: 8, w: 84, h: 84}`). The blank-frame photographs are shot consistently so this works as a launch-grade approximation.

**Phase 4 admin tooling** will let an admin edit `inner_rect_pct` per row to nudge alignment when art-direction reviews flag an off-center frame.

## Default selection

- **Size:** `16x20`
- **Finish:** `brown` (Prodigi color slug `brown`)
- **UI label for `brown`:** **"Walnut"** (override applied in `preview/routes.py::FINISH_DISPLAY_OVERRIDES`).

The marketing name "Walnut" is what's shown to customers. Internally, the SKU is `cf-{size}-brown`, the Prodigi color slug is `brown`, and the seeded display name in `prodigi_skus.finish` is `Brown`. Don't rename anything in the DB or the JSON — only the UI label changes.

## How to add a new SKU

1. Source the new blank photograph (1500–2400 px long edge, transparent or matched-background JPEG/PNG).
2. Drop it into `wildprint/assets/frames/` AND `wildprint/review_app/static/frames/` (until Phase 4 admin tooling automates the upload).
3. Add a row to `data/frame_skus.json` with all required fields. Use one of the existing same-finish rows as a template; adjust `size_inches`, `prodigi_sku`, `internal_sku`, asset URLs.
4. Add a corresponding row to the `prodigi_skus` Alembic seed (or insert via admin).
5. Run `pytest tests/preview/ -v` — the data validation tests will flag missing fields, file-not-found, etc.
6. (Optional, Phase 4) Fine-tune `inner_rect_pct` per-size if the photograph proportions differ.

## Demo mode

The `/preview/_demo` route uses a fixed sample poster and is **dev-only**. To enable:

```bash
export PREVIEW_DEMO_ENABLED=true
flask --app review_app.app run --port 8081
# visit http://localhost:8081/preview/_demo
```

When `PREVIEW_DEMO_ENABLED` is unset (or set to `0` / `false` / `no`), the route returns 404. Production never gets the demo.

## Wiring the blueprint

The blueprint is **not** auto-registered. Add this snippet to `review_app/app.py`, next to the other `_init_*` calls:

```python
from review_app.preview import init_app as _init_preview
_init_preview(app)
```

This registers:

- `GET /preview/<spec_hash>` — production configurator
- `GET /preview/_demo` — dev demo (404 unless flag enabled)
- `GET /preview/data/frame_skus.json` — catalog data
- `GET /preview/_health` — liveness probe

## Performance budget

| Op | Target | Notes |
|---|---|---|
| Initial page load | ≤ 800 ms LCP on 4G | All 8 blanks preloaded eagerly (~3 MB total) |
| Size change | ≤ 100 ms | Pure canvas redraw, no I/O |
| Finish change | ≤ 100 ms | Pure canvas redraw, no I/O (blank already in cache) |
| Repaint cost | ≤ 5 ms median | One `drawImage` for the frame + one for the poster |

Eagerly preloading 8 PNGs vs. lazy-loading on first switch is a deliberate tradeoff: it adds ~2 MB to the cold page load but guarantees every subsequent click is instant. For a configurator where most users try several finishes, that's the right cost shape.

## Accessibility

- `<canvas>` has `role="img"` + `aria-label="Poster preview in selected frame"`.
- Finish picker is a `role="radiogroup"`; each swatch is a `role="radio"` with `aria-checked` synced.
- Arrow-key navigation cycles through swatches; Enter/Space activates.
- All swatch images have non-empty `alt` (or empty `alt=""` when redundant) and a visible text label.
- Headline price has `aria-live="polite"` so screen readers announce the change.

## Coordination notes

The parallel Phase 2 backend agent owns:

- The `render_outputs` table (Alembic migration, `render_spec_hash` lookup).
- The tier-2 render job + Spaces upload.

This module **soft-imports** that agent's model — if the migration hasn't run yet, `/preview/<spec_hash>` returns 404 instead of crashing. Once the parallel agent's PR lands, the lookup just works.
