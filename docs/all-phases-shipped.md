# fishingposter.com — All Phases Shipped

**Status:** All 6 phases code-complete and CI green. ~87 commits across 6 branches. Staging app live. Production untouched.

---

## What landed, by phase

| Phase | Branch | Commits | CI |
|---|---|---|---|
| 0 — Foundation | `phase-0-foundation` | 16 | 🟢 |
| 1 — Prodigi client | `phase-1-prodigi-client` | 7 | 🟢 |
| 2 — Frame preview | `phase-2-frame-preview` | 14 | 🟢 |
| 3 — Order flow + checkout | `phase-3-checkout` | 17 | 🟢 |
| 4 — Admin shell + Prodigi pages | `phase-4-admin` | 13 | 🟢 |
| 5 — Production essentials + customer-facing | `phase-5-production` | 20 | 🟢 |

**Test suite (full):** 339 passed + 10 integration-gated skipped. mypy strict + ruff clean across all new modules.

---

## What's deployed where right now

| Surface | URL / location | State |
|---|---|---|
| Production wildprint | wildlife.5story.com | LIVE (legacy $49 unlock; untouched throughout) |
| Production fishingposter.com | fishingposter.com (DNS only) | LIVE same as wildlife.5story.com — Coolify app szncf7ts5gwk1idp2ubkgnvw also serves it |
| Staging | staging.fishingposter.com | Phase 0 deployed; Phase 1-5 branches need rollout |
| DO Spaces | nyc3 region | 3 buckets (posters/previews/thumbs) with API key, CORS, ACLs |
| Postgres | benedict-ventures container, port 5433 | `fishingposter` + `fishingposter_staging` databases |
| Resend | resend.com | Domain verified, SPF/DKIM/DMARC live, send-only key in env |
| Smarty | smarty.com | US Address Verification key saved |
| Prodigi | dashboard.prodigi.com + sandbox-beta-dashboard.pwinty.com | Live key + sandbox key both in env, webhook URL configured |
| Droplet | benedict-ventures (NYC1) | Upsized 2vCPU/4GB → 4vCPU/8GB/160GB (~$63/mo) |

---

## What's done, end-to-end

### Customer flow (UI plumbing in place; needs visual polish before launch)
1. Customer renders a poster (existing `/create` flow, untouched)
2. Visits `/preview/<spec_hash>` — sees the poster in a Classic Frame, picks size + finish from a swatch row, sees live price update (sub-100ms swap, no server round-trip)
3. Clicks "Add to Cart" → `/cart` → "Proceed to Checkout"
4. Fills shipping address — Smarty validates in real-time, rejects undeliverable
5. Pays via Stripe Checkout (multi-line-item, optional Stripe Tax)
6. Stripe webhook → outbox fan-out → tier-3 high-res render queued + Prodigi order created (sandbox or live per env) + confirmation email sent
7. Customer logs into `/account` (magic-link, no password) → sees orders, status timeline, invoice, shipping tracker
8. If something goes wrong: customer can request a reprint within 30 days of delivery; admin approves/rejects

### Admin flow (8 categories, ~62 routes)
- **Dashboard:** today's stat cards (orders, revenue, in-production, shipped, errors, AI spend)
- **Orders:** all orders + filters + CSV export, order detail with full Prodigi timeline + refund/reprint actions, refunds queue, sandbox test orders
- **Customers:** all customers + LTV, customer detail with addresses + email log + notes
- **Catalog:** Species (migrated from legacy), Backgrounds (Flux Pro Ultra + Real-ESRGAN), Sizing, Frame SKUs (32 launch SKUs with margin %), Lakes, Render presets
- **Fulfillment:** Prodigi connection settings, webhook log with replay, error queue with retry, reprints queue with approve/reject
- **Content:** Email templates (DB-backed via content_blocks), email send log, marketing slot editing
- **Analytics:** Sales (revenue/AOV/conversion), AI usage (per provider/cost/top render_specs), Operations SLA (real time-to-production from `orders.in_production_at`)
- **Settings:** Users + roles (admin/staff/viewer with hidden-not-greyed nav), API keys (env-var inventory), Integrations (real upstream probes with 5-min cache), Audit log (every admin POST/PATCH/DELETE recorded), My account (password change)
- Topbar: global search (Ctrl+K — placeholder for cross-entity search), Prodigi env pill (red SANDBOX / green PROD), notifications bell (placeholder for push), user menu

### Operations
- **Cron scheduler** (separate Coolify service via `Dockerfile.scheduler`):
  - `drain_outbox_job` every 30s
  - `refresh_all_skus_job` daily 03:00 UTC
  - `cleanup_old_render_outputs` weekly
  - `monitor_failed_callbacks` every 15 min (Sentry alert)
  - `monitor_dead_outbox` every 30 min (Sentry alert)
- **Render queue** (separate Coolify service via `Dockerfile.worker`): SIGTERM grace, fakeredis-tested
- **Rate limiting:** Flask-Limiter with Redis backend; render endpoints capped at 5/min, cart at 30/min, global at 120/min; admin + webhooks exempt
- **Audit log:** every admin write op recorded with user_id, action, target, before/after JSONB, IP
- **Daily Postgres backup:** `pg_dump | gzip` cron on droplet, 14-day retention
- **AI usage interceptor:** every OpenAI/Recraft/Replicate call logged with cost (shadow mode behind `AI_LOGGING_ENABLED`)

---

## Migration chain (21 migrations)

```
0001_baseline                    (empty)
0002_users                       (Phase 0.6 admin auth)
0003_ai_usage                    (Phase 0.10 AI logging)
0004_outbox                      (Phase 0.5 transactional outbox)
0005_prodigi_orders              (Phase 1: prodigi_orders, _skus, _callbacks, shipments)
0006_seed_prodigi_skus           (Phase 1: 32 launch SKUs)
0007_render_specs                (Phase 2: render_specs + render_outputs)
0008_customers                   (Phase 3a)
0009_addresses                   (Phase 3a + Smarty validation cols)
0010_carts                       (Phase 3a: carts + cart_items)
0011_orders                      (Phase 3a: orders + order_items + FK from prodigi_orders)
0012_refunds                     (Phase 3a)
0013_migrate_leads_json          (Phase 3a: idempotent + reversible data migration)
0014_stripe_events               (Phase 3b: webhook dedup)
0015_audit_log                   (Phase 5a)
0016_customer_login_tokens       (Phase 5b: magic-link auth)
0017_reprints                    (Phase 5b: reprint_requests)
0018_content_blocks              (Phase 5b: replace in-memory templates)
0019_notes                       (Phase 5b: orders + customers)
0020_orders_production_ts        (Phase 5b: orders.in_production_at for real ops metric)
0021_reprint_cost                (Phase 5b: orders.internal_cost_cents)
```

---

## What's NOT done (deferred follow-ons, in priority order)

**Hard blockers for production launch:**
1. **Roll out branches to staging.** `phase-3-checkout`, `phase-4-admin`, `phase-5-production` need to be deployed on the staging Coolify app. Currently staging only has Phase 0 code. Each branch's CI is green — deploys are mechanical.
2. **Production cutover plan.** Decide when wildlife.5story.com → fishingposter.com brand switch happens. Existing $49 unlock keeps working throughout (untouched by design).
3. **Stripe production keys for physical orders.** The current Stripe key handles the $49 unlock. Confirm it has the right product/tax setup for the new physical SKUs, or set up a parallel Stripe product line.
4. **Visual polish.** All admin + customer-facing templates are functional and accessible but unstyled. Phase 6 (or design-shotgun work) would polish branding before launch.

**Soft (can wait):**
5. **Real cmd+K cross-entity search** (current is a placeholder)
6. **Notifications polling endpoint + JS hookup** (notifications bell is a stub)
7. **Render preset editing in admin** (currently read-only)
8. **2FA + per-user API tokens** on My account
9. **Per-line-item reprint selection UI** (data model supports it; UI passes through whole order)
10. **PDF wildlife guide** (parallel project per `project_pdf_guide.md` memory — separate workstream)
11. **Real upstream probe verification** against live services from staging droplet (probes are wired but agent dev box couldn't run them with creds)

---

## How to deploy each branch to staging

For each branch in order (3 → 4 → 5), use the existing Coolify staging app `wildprint-staging` (UUID `vlweqt7q9wi7e43jtkg6zodn`):

1. In Coolify UI: app → Source → change branch from `phase-0-foundation` to `phase-3-checkout`. Click Deploy.
2. Wait for build + healthcheck. Migrations run automatically on container start (Phase 0 added `alembic upgrade head` to the CMD).
3. Once green, repeat for `phase-4-admin` then `phase-5-production`.
4. Add the new env vars Phase 5 needs:
   - `STRIPE_TAX_ENABLED=true` (after configuring Stripe Tax registration)
   - `STRIPE_WEBHOOK_SECRET_V2=whsec_...` (new webhook for physical orders; create in Stripe → Webhooks → add endpoint pointing at `https://staging.fishingposter.com/webhook/stripe/v2`)
   - `RATE_LIMIT_RENDER_PER_MIN=5`, `RATE_LIMIT_CART_PER_MIN=30` (defaults are fine; override if needed)
   - `STRIPE_CUSTOMER_AUTH_JWT_SECRET=<random>` (for the magic-link tokens — Phase 5b reads this)
5. Add a third Coolify service: `wildprint-staging-scheduler` using `Dockerfile.scheduler`, Replicas=1 (rq-scheduler has no leader election), same env vars as web/worker.

---

## Branch / merge strategy

Each phase is its own branch off the prior phase. Suggested merge order to `main`:

```
main ← phase-0-foundation ← phase-1-prodigi-client ← phase-2-frame-preview
     ← phase-3-checkout ← phase-4-admin ← phase-5-production
```

Each merge can be a PR for review. CI passes at every step. Or merge them all to a single `release-v0.2` branch and PR that to main as one logical "wave."

The `main` branch hasn't moved — currently still at the pre-Phase-0 state (the existing wildprint app powering wildlife.5story.com).

---

## Next move

Whenever you have time:
1. Pull the branches locally and skim the diffs (or open them as PRs in GitHub)
2. Roll Phase 3 → 4 → 5 onto staging Coolify and click around the UI
3. Tell me what to polish, redirect, or ship to production

That's it. The plumbing is done.
