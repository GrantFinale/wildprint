# fishingposter.com — Prodigi Integration & Admin Refactor Plan

**Status:** Phase 0 code-complete (10 commits on `phase-0-foundation`, pushed to GitHub 2026-05-05). Staging + integration smoke deferred pending external accounts. See [docs/phase-0-done.md](phase-0-done.md) for status.
**Date:** 2026-05-05

---

## Decisions locked (2026-05-05)

| # | Decision | Choice | Implication |
|---|---|---|---|
| 1 | **Stack** | Keep Flask + add strict mypy + Pydantic + SQLAlchemy/Alembic for all new code | Renderer untouched; new commerce code hits the type-safe bar |
| 2 | **Frame preview** | Client-side composite of Prodigi blanks + poster (canvas/WebGL) | All 8 finishes' assets already in `wildprint/assets/frames/` — no commissioning |
| 3 | **$49 unlock** | Bundle: free hi-res digital download with any physical purchase; $49 standalone digital remains | Existing checkout extends; physical adds line items, doesn't replace |
| 4 | **Launch SKUs** | 4 sizes × **all 8 finishes** = 32 SKUs (Classic Frame line) | Larger SKU table than originally scoped; finish names verbatim from Prodigi |
| 5 | **Render resolution** | **Three-tier**: thumbnail (400 px) for menus/pickers; preview (2400 px @ 72 DPI, watermarked, with 1800 px srcset fallback) for the configurator; print (7200×10800 @ 300 DPI) only after `payment_intent.succeeded` | Crisp on retina/4K monitors AND scrape-resistant (max printable from preview = 8" with watermark) |
| 6 | **Finish naming** | Use Prodigi's verbatim names (Black, White, Natural, Antique Silver, Brown, Antique Gold, Dark Grey, Light Grey) | No translation layer; zero risk of customer-vs-shipped mismatch |
| 7 | **Email** | **Resend** | Self-host on existing mail-server droplet was investigated and ruled out: DO blocks outbound port 25 + 587, no PTR, droplet is already serving journeyperfect.com/5story.com personal mail via maddy. Resend gives 3k/mo free, sub-10-min setup, three DNS records on fishingposter.com via `doctl`. |
| 8 | **Address validation** | Smarty | ~$0.005/lookup, autocomplete widget; 250 free for testing |
| 9 | **Production domain** | `fishingposter.com` (single source of truth) | wildlife.5story.com retired; fishingposter.com already DO-managed and pointed at the benedict-ventures droplet |
| 10 | **External accounts** | Fresh signups for Resend, Smarty; **DO Spaces for object storage (not R2)** — aligns with parallel PDF guide work | All under benedictmt@gmail.com / DO-linked billing |
| 11 | **Watermark style** | Subtle diagonal "fishingposter.com" text @ ~10% opacity | Confirmed — see render tier 2 in architecture section |
| 12 | **Staging environment** | Separate Coolify app at `staging.fishingposter.com` on same droplet | Same Postgres container, separate `fishingposter_staging` DB; Prodigi sandbox + Stripe test mode |

### Architectural consequences of decision #5 (three-tier render)

The renderer needs **three output modes**, all derived from one canonical `render_spec`:

| Tier | Long edge | DPI | Format | Watermark | Storage | When generated |
|---|---|---|---|---|---|---|
| **1. Thumbnail** | 400 px | 72 | JPEG ~50 KB | none (size is its own protection) | CDN-cached, public | Lazily on first request; cached forever per `render_spec` hash |
| **2. Preview** | **2400 px** (with 1800 px srcset fallback for non-retina) | 72 | JPEG ~600 KB @ q85 | subtle diagonal "fishingposter.com" + low opacity | CDN-cached, public, signed URL with 24 h TTL | Once per "generate" click; cached per `render_spec` hash |
| **3. Print** | 7200×10800 | 300 | PNG, sRGB | **none** | S3/R2 private bucket, signed URL handed to Prodigi (~7-day TTL matches Prodigi's 30-day asset retention) | Only after `payment_intent.succeeded` webhook fires |

**Why these specs:**
- **Thumbnail at 400 px @ 72 DPI**: max printable at 300 DPI = ~1.3 inches. Useless to scrape. Page-grid loads stay sub-100 KB total even with 20 thumbnails on screen.
- **Preview at 2400 px @ 72 DPI**: crisp on retina/4K monitors at full configurator viewing sizes (a 1200 CSS-px display on a 2× retina screen needs 2400 device pixels — exact match). Serves a 1800 px variant via `<img srcset>` to 1× displays to save bandwidth. Max printable from 2400 px at 300 DPI = 8" diagonal — covers a postcard, nothing larger; watermark deters even that. Tier 3 has 9× the pixel area, so the post-payment hi-res is unmistakably better.
- **Print at 7200×10800 @ 300 DPI**: covers the largest launch SKU (24×36") at full Prodigi quality. PNG is required because Prodigi accepts JPG/PNG/PDF only and PNG preserves transparency in the master art. (~30-60 MB per file — hence the post-payment-only rule.)

**Implications:**
- **Renderer needs a `tier` parameter.** Same `render_spec` → three deterministic outputs. Use one PIL composition pipeline with a final resize+compress step per tier.
- **Render queue is mandatory in Phase 0** — synchronous high-res renders inside a webhook handler will time out. Tier 1 and Tier 2 are fast enough to render synchronously on the request thread (or via a small thread pool); Tier 3 must be queued.
- **Persist `render_spec` on the order** as a JSON column (lake, species, style, layout config, frame SKU, all randomness seeds). Tier 3 reproduces tier 2 exactly — pixel-equivalent (within compression tolerance).
- **Cache aggressively by `render_spec` hash.** Tier 1 and Tier 2 outputs are content-addressable. If the same spec is requested twice, return the cached file. Cache invalidation = changing the spec.
- **Watermark policy**: subtle diagonal text, ~10% opacity, large enough to be a hassle to remove cleanly but not so loud it kills the preview's appeal. Confirm visual treatment before Phase 2 ships.
- **Anti-scrape friction (defense in depth, not security):** disable right-click → save image, serve preview through a signed URL with short TTL, set `Cache-Control: private`, omit EXIF. None of these stop a determined scraper, but they raise the bar above "kid with browser DevTools."
- **Regression test**: render the same `render_spec` at all three tiers, assert composition identical (downsampled tier 3 ≈ tier 2 ≈ scaled tier 1). Catches drift if the renderer changes.

---

## TL;DR — three things to know

1. **Stack reality vs quality bar.** The codebase is **Python 3.11 / Flask / Jinja / PIL / JSON files**, not TypeScript. The brief asks for "type-safe end to end (TypeScript strict or equivalent)." Three options: (a) keep Flask, adopt strict mypy + Pydantic for the new code; (b) build the new commerce surface as a separate TS/Next.js service that calls the existing Python renderer; (c) port the whole thing to Next.js. **Recommendation: (a)** — the renderer is the crown jewel, leave it; bolt strict-typed Python on for everything new.
2. **Prodigi has no 3D mockup API. Period.** "3D mockups" is dashboard-only marketing language for pre-rendered 2D images from multiple camera angles. No interactive/spinnable endpoint, no programmatic mockup generation with our asset. Phase 2 must be **client-side compositing in our UI** with overlay PNGs we own. No fallback to "use Prodigi's mockup API" — it doesn't exist as an API. (See §2 below.)
3. **There is no order/customer/email infrastructure.** Today: $49 Stripe unlock → flag in `metadata/leads.json` → session cookie. To sell physical posters we need a real orders DB, a real email pipeline, persistent asset storage, and address validation. Treat Phase 5 ("Supporting Infrastructure") as a **prerequisite** for Phases 1–4, not an afterthought.

---

## 1. Codebase audit

### Stack
| Layer | What's there |
|---|---|
| Backend | Flask 3.0, Gunicorn (2 workers × 4 threads), port 8080 ([Dockerfile#L54-55](../Dockerfile#L54)) |
| Frontend | Jinja2 + vanilla JS in templates. No SPA. |
| DB | **None.** State lives in `metadata/leads.json` and `metadata/admin_settings.json` ([app.py#L333](../review_app/app.py#L333)) |
| Auth | Session cookie only ([app.py#L1042](../review_app/app.py#L1042)). Admin panel has **no gate**. |
| Payments | **Stripe already wired** — checkout sessions + webhook receiver ([app.py#L1034-1154](../review_app/app.py#L1034)). Single $49 product (`STRIPE_PRICE_ID`). |
| Email | None. No Resend/SendGrid/SMTP/Postmark. |
| Storage | Local FS: `output/posters`, `output/uploads`, `output/backgrounds` (Docker volume) |
| AI | OpenAI, Recraft, Replicate (Flux Pro Ultra + Real-ESRGAN 4x for backgrounds) |
| Hosting | Coolify on DO droplet `benedict-ventures` → `wildlife.5story.com` |
| Tests | **Zero.** No pytest, no test files. |
| CI | **None.** No `.github/workflows`. |

### Rendering engine (LEAVE ALONE)
- Lives in `poster_layout/renderer.py` — `PillowPosterRenderer`, `EditorialPosterRenderer`, `EditorialMultiRenderer`.
- Output: **PNG, 5100×3300 px** (~17×11 in @ 300 DPI). Server-side via background job + `/stream/{job_id}` SSE poll.
- Inputs: lake, species slug, art style, master image, frame overlay, background image.
- Persisted to `output/posters/{job_id}.png` on the local volume.
- **Implication for Prodigi:** 5100×3300 covers most Classic Frame sizes up to ~17×11 at 300 DPI. For 16×24" (needs 4800×7200 px) and 20×30" (needs 6000×9000 px), **the current resolution is insufficient for portrait-orientation large frames**. Verify orientation handling and decide whether to upscale (Real-ESRGAN already in stack) or restrict the SKU set we sell.

### Existing admin (must preserve in refactor)
Routes today, all under `/admin*`, no auth gate:
- `GET /admin` — species table, master image coverage, scaling controls
- `GET /admin/data` — JSON for the table
- `POST /admin/settings/global_size_variance` — global scaling factor
- `POST /admin/species/<slug>/scale` — per-species size adjust
- Background gallery: preset picker + Flux Pro Ultra generation button

It's a single page with vanilla-JS interactivity. Functional but **completely unprotected**. Adding auth is non-negotiable before adding order/customer/PII data.

### Existing checkout flow
1. `POST /api/create-checkout-session` → Stripe checkout for $49 unlock with metadata `{email, lake_name, state}`.
2. Success → `GET /checkout/success` → verify, mark `leads.json` paid, set cookie `unlocked=True`.
3. `POST /webhook/stripe` → idempotently re-marks paid.

This is a **digital unlock model**, not a physical-product cart. For Prodigi we're effectively building a new commerce flow alongside it (or replacing it).

### Gaps the Prodigi work depends on
- No orders table, no line items, no order history page for customers
- No transactional email at all
- No persistent asset storage outside the Docker volume (lose volume = lose every poster ever rendered)
- No address validation
- No CI, no tests, no staging env
- No admin auth or RBAC
- No AI usage / cost logging (OpenAI + Recraft + Replicate are all metered per call)
- Single-tenant filesystem — won't survive a droplet rebuild

---

## 2. Prodigi capability findings

Source: https://www.prodigi.com/print-api/docs/ (verified by direct fetch).

### 2a. Mockups — **the headline finding**
- **There is no Mockup API.** `prodigi.com/mockup-api/` returns 404. Mockups are a dashboard feature only.
- "3D mockups" in marketing copy = **2D images shot from multiple angles**, not interactive (https://www.prodigi.com/mockups/).
- AI mockups exist (15 free, unlimited on Prodigi Pro) — also dashboard-only still images.
- **What this means for us:** the frame preview UI in Phase 2 must be 100% client-side compositing with overlay assets we own or commission. There is no "fall back to Prodigi's Mockup API" option — it doesn't exist.

### 2b. Catalog
- **No `/products` list endpoint.** You GET `/v4.0/products/{sku}` per known SKU.
- Full catalog only via PDF: https://www.prodigi.com/download/product-range/prodigi-portfolio.pdf.
- US-fulfilled framed families: **Classic frames**, **Box frames**, **Budget framed poster** (https://www.prodigi.com/products/wall-art/framed-prints/).
- Classic frame finishes: Black, White, **Natural**, Antique Silver, Brown, Antique Gold, Dark Grey, Light Grey. ⚠️ Note: **Prodigi does not have "walnut" or "oak"** — closest is "Natural" or "Brown". The brief's "default walnut" needs to map to `Natural` or `Brown`.
- SKU pattern: `GLOBAL-CFPM-16X20` + attribute `{"color":"black"}`.
- Glaze size = listed size (not outer dim). Mat sizes auto-applied per FAQ.

### 2c. Pricing
- No static price list. Use **`POST /v4.0/quotes`** — returns `costSummary`, `unitCost`, lab, carrier per shipping tier.
- Implication: every preview price update either uses (a) a cached SKU price table we refresh nightly, or (b) a live quote at checkout only. **Recommendation: (a) for the preview, (b) for the order confirmation.**

### 2d. Webhooks ("Callbacks")
- Set `callbackUrl` on order create. CloudEvents-formatted POSTs.
- Only **3 events fire**: order created, shipments made, order completed. (Not the full lifecycle the brief implies.)
- **No signature verification documented.** No HMAC, no signing secret. **Treat as untrusted pings — always re-fetch `GET /v4.0/orders/{id}` before mutating state.** Persist event IDs for dedupe.
- No documented retry/SLA.

### 2e. Sandbox
- `api.sandbox.prodigi.com` (live: `api.prodigi.com`). Different keys.
- Sandbox doesn't print or charge. Status transitions triggerable via `sandbox-beta-dashboard.pwinty.com`.
- Rate limits: undocumented.

### 2f. Image upload
- **Pull model only** — we provide a URL, Prodigi downloads (10 retries) and stores 30 days.
- **JPG, PNG, PDF only.** No TIFF.
- Resolution per SKU exposed via product details (`printAreaSizes.{area}.{horizontalResolution,verticalResolution}`). Example: 16×24" needs 4800×7200 px @ ~300 DPI.
- Auto-bleed (3 mm) if not pre-applied.
- **Color profile not documented** (assume sRGB; confirm with support).
- `md5Hash` supported for integrity check.
- Sizing modes: `fillPrintArea` (default), `fitPrintArea`, `stretchToPrintArea`.

### 2g. Shipping (US)
- Tiers: `Budget`, `Standard`, `StandardPlus`, `Express`, `Overnight`.
- **All US orders tracked regardless of tier.**
- Carrier returned per shipment in `shipments[].carrier.{name,service}` — lab decides (USPS/UPS/FedEx).
- Tracking number + URL on `shipments[].tracking.{number,url}`.
- Lead times not in API; per-product on website only.

### 2h. Other gotchas
- **No address validation.** Bad address = we pay for re-shipping. Validate pre-submit (Smarty/Lob/USPS).
- **Cancellation window is tiny** — only before `inProduction` flips to `InProgress`. Use **Pause an order** + dashboard pause window (e.g. 30 min) to give customers a real cancel window.
- **Returns: only damaged/incorrect items.** Customer-error reprints = a new order on our dime unless we charge them.
- Errors surface in `status.issues[]` with `errorCode` (e.g. `order.items.assets.NotDownloaded`). Image quality rejections come via support, not webhook.
- **Idempotency:** supply `idempotencyKey` on order create.

---

## 3. Proposed phase order with rough estimates

Estimates are person-days for a single experienced engineer, including tests. They assume option (a) above (Python + strict mypy/Pydantic).

### Phase 0 — Foundation (NEW — must come first) · ~6–8 days
The brief calls this Phase 5 but the Prodigi work cannot land without it.
- **Postgres** (use the existing `benedict-ventures` droplet container on port 5433). Add SQLAlchemy + Alembic. ~1d
- **Persistent asset storage**: S3 or Cloudflare R2 for high-res posters (lifecycle: keep 18 months for reprint disputes). ~1d
- **Transactional email**: Resend (cheapest, dev-friendly). Branded templates: order confirmed / in production / shipped / delivered / refunded / problem. ~1.5d
- **Admin auth + RBAC** (admin/staff/viewer). ~1d
- **Pytest + GitHub Actions CI** (lint, mypy strict, pytest, build). ~1d
- **Structured logging + Sentry** (or equivalent) for the new code paths. ~0.5d
- **AI usage logging table** + interceptor on every OpenAI/Recraft/Replicate call (record tokens or units, computed cost, user, job_id). ~1d

### Phase 1 — Prodigi API client · ~5–6 days
- Typed client (Pydantic models for every request/response). Sandbox + prod via env.
- Wrappers: `create_order`, `get_order`, `cancel_order`, `pause_order`, `quote`, `get_product`.
- Exponential-backoff retry for 5xx/timeouts; idempotency keys on create.
- `prodigi_orders` table (our order_id ↔ Prodigi `ord_*`, status, last-fetched, raw JSON snapshot).
- `prodigi_skus` table (internal SKU ↔ Prodigi SKU, retail price, last-quoted wholesale, computed margin, refresh timestamp). Nightly refresh job hits Quote endpoint per SKU.
- Webhook receiver: dedupe on event ID, **re-fetch order via GET before mutating**, dispatch to handlers.
- Mocked unit tests + sandbox integration tests (gated behind `PRODIGI_SANDBOX_KEY` in CI).

### Phase 2 — Frame preview · ~5–7 days (+ overlay assets, see open Q1)
- Client-side compositor in canvas (or pixi.js for crisp scaling). Sub-100ms response on dropdown changes.
- Frame geometry data-driven (`frame_skus.json`: SKU, inner-rect bounds, mat width, overlay asset URL at 1×/2×/4×).
- Default: walnut → resolved to Prodigi's `Natural` or `Brown` finish (decision needed, see Q2).
- Live price update from cached SKU table; re-quoted at checkout.
- **End-to-end regression test:** snapshot the preview compositor output, snapshot the asset POSTed to Prodigi, assert pixel-equivalent (within tolerance) for a fixture poster + SKU combo.
- Overlay assets: see Open Question 1.

### Phase 3 — Order flow & checkout · ~6–8 days
- Cart (multi-item; each item = poster_id + frame_sku + qty).
- Address form with Smarty/Lob US validation.
- Stripe Checkout (already wired; extend to multi-line-item, dynamic amount from our quote).
- On `payment_intent.succeeded`: create Prodigi order, persist mapping, queue confirmation email.
- Webhook handlers for the 3 Prodigi events → status updates + customer emails.
- Rejection recovery: customer-facing flow for image-quality / address rejections (re-upload or re-enter).
- **Coexistence with the $49 unlock:** decide whether to retire it or keep both (see Q3).

### Phase 4 — Admin refactor · ~7–10 days
- **4a. IA proposal first** (no code) — wireframe-level doc covering nav structure, get sign-off.
- **4b. New shell** — sidebar + topbar + breadcrumbs + global search + RBAC. Migrate existing species/background/scaling pages into Catalog category. No behavior change during migration.
- **4c. New Prodigi pages** — connection settings, SKU/price table with margin, order detail with status timeline, webhook event log, error queue with retry actions.

### Phase 5 — Remaining infrastructure · ~4–5 days
Most of this moved into Phase 0. Remaining:
- **US sales tax** — Stripe Tax (simplest). ~1d
- **Returns/reprint workflow** — admin-initiated; integrates with Prodigi's damaged-item process. ~1.5d
- **Refund handling** tied to Stripe + Prodigi cancel/pause. ~1d
- **Rate limiting** on `/api/generate-poster` (per-IP + per-user). ~0.5d
- **Test order mechanism** — a flag on the order that routes to sandbox + skips Stripe. ~0.5d
- **AI usage admin page** — read from the Phase-0 logging table. ~0.5d

**Total rough estimate:** ~33–44 person-days. Realistic calendar with QA and back-and-forth: **8–11 weeks**.

---

## 4. Open questions — RESOLVED

All 8 original open questions answered 2026-05-05; see "Decisions locked" table at the top of this document. Only follow-up: viability of self-hosted email on the mail-server droplet (in flight).

---

## 5. Risks & unknowns

- **Prodigi callback security.** No HMAC = anyone who guesses our URL can spoof an event. Mitigate with: (a) unguessable callback path, (b) always re-fetch the order, (c) IP allow-list if Prodigi publishes one (ask support).
- **Prodigi rate limits unknown.** Sandbox testing won't reveal prod limits. Build a token-bucket rate limiter into the client from day one.
- **Color profile undocumented.** Customer expectations on color match are high. Send a test order in sandbox → live conversion early, get a physical print, evaluate before committing to launch SKUs.
- **Lead times not in API.** Customer-facing "ships by" dates require maintaining our own table per SKU. Stale data = angry customers.
- **`prodigi_orders` raw-JSON snapshots will grow fast.** Plan retention (e.g., truncate to last 90 days; keep status fields forever).
- **Renderer is a subprocess/threading model in a 2-worker Gunicorn.** Concurrent render demand could starve checkout. Move heavy renders to a queue (Celery/RQ/Dramatiq) before launch traffic.
- **Single droplet, no staging.** Phase 0 should add a staging Coolify app on the same droplet pointing at the Prodigi sandbox.
- **No backups of `metadata/leads.json` or `output/`** that I can see. If we go live before Phase 0 finishes, **one rm -rf and the business is gone.** Take a backup today regardless.

---

## 6. What I will NOT do until you sign off

- Write any feature code
- Run any database migrations
- Commit or rotate any API keys
- Touch the rendering engine
- Begin the admin refactor

When you're ready, reply with: answers to the 8 questions in §4, any phase-order changes, and a go for Phase 0.
