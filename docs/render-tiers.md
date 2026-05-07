# Render tiers — three-tier render system (Phase 2)

The renderer produces three deterministic outputs from one canonical
`RenderSpec`. Two customers requesting the same lake / species / style get
the same `spec_hash` and we serve the cached file.

## Tier table

| Tier | Long edge | DPI | Format | Watermark | Bucket | When generated |
|---|---|---|---|---|---|---|
| 1 — thumb | 400 px | 72 | JPEG q85 | none | `fishingposter-thumbs` (public-read) | Lazily on first request, cached forever per `spec_hash` |
| 2 — preview | **2400 px** (1800 px srcset variant deferred) | 72 | JPEG q85 | diagonal `fishingposter.com` @ 10% opacity | `fishingposter-previews` (public-read) | Once per "generate" click, cached |
| 3 — print | 7200 x 10800 | 300 | PNG sRGB | **none** | `fishingposter-posters` (private, signed URL) | Only after Stripe `payment_intent.succeeded` (queued via RQ) |

Bucket names resolve from `SPACES_THUMBS_BUCKET`, `SPACES_PREVIEWS_BUCKET`,
`SPACES_POSTERS_BUCKET` respectively (defaults match the table above).

## Cache behavior

Cache key: `(spec_hash, tier)` via `UNIQUE(render_spec_id, tier)` on
`render_outputs`. A hit returns the storage URL directly:

* Tier 1/2: public URL of the form
  `https://<bucket>.<region>.digitaloceanspaces.com/<key>`.
* Tier 3: pre-signed GET URL with 1 h TTL (Prodigi handoff uses a longer TTL —
  see Phase 3).

The `spec_hash` is a SHA-256 of the canonical JSON serialization of:

```
{lake, species (sorted), art_style, layout_config, renderer_version}
```

NFC-normalized strings, sorted dict keys, no whitespace. Re-ordering
`layout_config` keys does NOT change the hash. Re-ordering `species` does
NOT change the hash (the renderer treats it as a set).

### Cache invalidation: bumping `renderer_version`

`renderer_version` is part of the hash. Bump it to force a full regen.

**When to bump:**
* Renderer logic change that alters pixels (font swap, layout tweak,
  watermark tuning, color correction, anything visible).
* Tier downscale algorithm change (BICUBIC -> LANCZOS, JPEG quality, etc.).

**When NOT to bump:**
* Logging, caching internals, type hints, refactors.
* Storage key changes (those are independent of pixel output).

**How to bump:**

1. Edit `RENDERER_VERSION_DEFAULT` in `review_app/render/spec.py`.
   The constant is module-level so a single grep finds every reference.
2. Commit with the convention:

   ```
   phase N: bump renderer_version to vX (reason)
   ```

3. The next request for any spec re-renders all tiers. Old `render_outputs`
   rows aren't deleted automatically — they stay around as orphans (the new
   spec hash differs, so the lookup misses). Clean up via:

   ```sql
   DELETE FROM render_outputs WHERE generated_at < '<deploy date>';
   ```

   (Future Phase 4 admin page will wrap this.)

## Memory + perf budgets

| Tier | Target render time | Peak working memory | Output size budget |
|---|---|---|---|
| 1 — thumb | < 200 ms | < 50 MB | <= 100 KB |
| 2 — preview | < 1 s | < 200 MB | <= 700 KB |
| 3 — print | < 90 s | **<= 2 GB** | 30-60 MB (PNG) |

Tier 3 is the only memory-sensitive path. The renderer:

* Allocates one master canvas (RGB 7200x10800 = 233 MB raw).
* Calls `Image.save(..., format="PNG")` directly into a `BytesIO`.
* Does NOT keep the master alive after encoding.

PIL's PNG encoder copies the canvas internally during encoding; peak RSS
during a tier-3 render typically settles around 700 MB - 1.2 GB on the
droplet. The 2 GB cap leaves headroom for one concurrent tier-3 plus the
worker's baseline Python overhead (~250 MB).

## Wiring into Flask

Phase 2 ships `review_app/render/init_app(app)` as a logging-only no-op so
`review_app/app.py` stays untouched. Wire it in the next pass:

```python
# review_app/app.py
from review_app.render import init_app as init_render

# ...inside create_app() after the storage init...
init_render(app)
```

## Inspecting cached output (debugging)

Until the Phase 4 admin page lands, debug via psql + the DO Spaces console:

```sql
-- Find a spec by lake
SELECT id, spec_hash, renderer_version, created_at
FROM render_specs
WHERE canonical_inputs->>'lake' = 'Lake Hopatcong'
ORDER BY created_at DESC
LIMIT 5;

-- See its tier outputs
SELECT tier, storage_bucket, storage_key, file_size_bytes, generated_at
FROM render_outputs
WHERE render_spec_id = '<uuid>'
ORDER BY tier;
```

Then construct the URL manually:

```
https://<bucket>.nyc3.digitaloceanspaces.com/<storage_key>
```

(public buckets) or use `aws --endpoint-url=https://nyc3.digitaloceanspaces.com
s3 presign s3://<bucket>/<key>` for the private posters bucket.

## Watermark

Diagonal repeating "fishingposter.com" at 10% opacity, -30 degrees, ~600 px
tile spacing. White text — visible on dark backgrounds, subtle on light
ones. See `review_app/render/watermark.py` for the implementation; it's a
pure function with no DB or storage dependencies and is unit-tested
independently in `tests/render/test_watermark.py`.

This is **defense in depth, not security**. A determined scraper can strip
the watermark. The point is to raise the bar above "kid with browser
DevTools" — combined with private signed URLs for the post-payment master,
this is acceptable.

## RQ job registration

Tier-3 renders are enqueued via the existing `review_app.queue` wrapper.
The job function lives at `review_app.render.jobs.render_tier_job` and is
re-exported from `review_app.queue.jobs.render_tier_job` so the worker
process resolves it under either import path.

The Phase 0/1 `render_print_job` stub has been replaced with a thin
delegating wrapper that calls into `review_app.render.jobs.render_print_job`
— jobs enqueued under the old name continue to work.
