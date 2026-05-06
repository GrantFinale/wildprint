# Storage keys & bucket layout

Phase 0.3 ships the DigitalOcean Spaces wrapper for fishingposter.com.
This doc is the source of truth for *where things live* and *how to name them*.

## Provider

**DigitalOcean Spaces** (S3-compatible) in `nyc3`. Decision locked
2026-05-06 — see `memory/project_storage.md`. Cloudflare R2 was the
original plan, redirected to align with the parallel PDF guide
workstream.

Endpoint: `https://nyc3.digitaloceanspaces.com`
Region: `nyc3`
Auth: SigV4, env vars `SPACES_ACCESS_KEY_ID` / `SPACES_SECRET_ACCESS_KEY`.

## Buckets

| Bucket | Purpose | Visibility | CORS | Lifecycle |
|---|---|---|---|---|
| `fishingposter-thumbs` | Tier 1, 400 px JPEG thumbnails | public-read | fishingposter.com + staging + localhost | none (cache forever) |
| `fishingposter-previews` | Tier 2, 2400 px watermarked JPEG previews | public-read | fishingposter.com + staging + localhost | none (cache forever) |
| `fishingposter-posters` | Tier 3, 7200×10800 PNG print masters | private (signed URLs only) | n/a | **18-month retention** (matches Prodigi's max asset window) |

The PDF guide workstream owns `fishingposter-guides` separately — do not
touch from this codebase.

## Tier → bucket

```python
from review_app.storage.buckets import bucket_for_tier
bucket_for_tier(1)  # fishingposter-thumbs
bucket_for_tier(2)  # fishingposter-previews
bucket_for_tier(3)  # fishingposter-posters
```

## Key layout

All content-addressable keys are sharded with a 2-char hex prefix derived
from the hash. See `review_app/storage/keys.py` for the helpers.

```
thumbs/<shard>/<render_spec_hash>.jpg          # tier 1
previews/<shard>/<render_spec_hash>.jpg        # tier 2
prints/<order_id>/<render_spec_hash>.png       # tier 3
```

Where `<shard>` is `render_spec_hash[0:2].lower()` (256 evenly
distributed prefixes).

### Why shard?

S3-compatible backends partition by key prefix. Without a shard, every
preview lands under `previews/`, forcing all writes through one
partition and capping list/throughput. The 2-char shard yields 256 hot
prefixes that scale linearly with object count.

This is the same trick git uses for `.git/objects/<2>/<rest>` and what
AWS has recommended since 2011.

Cost: 2 extra bytes per key, no impact on fetch latency.

### Why key tier 3 by `order_id` (not just hash)?

Print masters are one-shot — there's no reuse benefit, and grouping by
order makes manual audits trivial:

```
aws s3 ls s3://fishingposter-posters/prints/ord_abc123/
```

If something goes sideways with a Prodigi job we can find every asset
that order ever uploaded.

## Retention

| Tier | Retention | Rationale |
|---|---|---|
| 1 (thumbs) | forever, with `Cache-Control: public, max-age=31536000, immutable` | Content-addressable. Cheap. |
| 2 (previews) | forever, same cache headers | Content-addressable. Cheap. Watermarked. |
| 3 (prints) | **18 months from creation**, lifecycle rule | Prodigi's max asset retention is ~30 days; 18 months covers customer reprint requests + chargeback windows. After that, delete; we can always re-render from `render_spec` JSON on the order. |

Lifecycle rules are configured directly on the bucket (see
`scripts/setup_spaces_lifecycle.sh`) and not enforced by application code.

## Signed URLs

| Use | TTL | Method |
|---|---|---|
| Tier 2 preview served on configurator | 24 h | GET |
| Tier 3 print URL handed to Prodigi | ~7 days (matches Prodigi retention) | GET |
| Direct browser uploads (rare) | 5 min | PUT |

`review_app.storage.get_signed_url(bucket, key, expires_in, method)` is
the single entrypoint.

## Public URLs

Tier 1 and tier 2 buckets are `public-read`. Public URLs are
deterministic:

```
https://<bucket>.nyc3.digitaloceanspaces.com/<key>
```

Eventually `cdn.fishingposter.com` will CNAME to the Spaces CDN edge for
tier 1 and tier 2; until then, direct origin URLs are fine.
