# Staging QA Report

**Date:** 2026-05-07 14:05 UTC
**Branch:** phase-6-polish
**Staging URL:** https://staging.fishingposter.com
**Reporter:** Claude QA agent (report-only mode)

## Containers (staging)

| Role | Container | Image SHA |
|---|---|---|
| Web | `vlweqt7q9wi7e43jtkg6zodn-134707612366` | `4884b03` |
| Worker | `lm0pb6em87w7f6rddn00fe23-132905795928` | `cf21c69` |
| Redis | `dk1c6msr50uy34mag06w7gf1` | `redis:7-alpine` |
| Postgres | `o630hdmppejmchbw7gn2qmn2` | `postgres:16-alpine` |
| Scheduler | **MISSING — see Deferred** | — |

(Note: `szncf7ts5gwk1idp2ubkgnvw` is the *production* `wildprint` app at wildlife.5story.com / fishingposter.com. Don't touch it.)

## Summary

- Total checks attempted: ~50
- Pass: 28
- Warning: 7
- Fail (blocker): 6
- Deferred: 1 (scheduler container)

**Verdict: NOT ship-ready.** Six blockers, three of which are user-facing on day-one customer flows.

---

## Findings

### Blockers (must fix before production)

#### B1. Configurator boot fails on `/preview/_demo` due to missing static asset
- **Repro:** `curl -I https://staging.fishingposter.com/static/sample/sample-poster.jpg` → `404`.
- **Console:** `[configurator] boot failed Error: failed to load image: /static/sample/sample-poster.jpg at img.onerror (.../preview/configurator.js:126:32)`
- **Impact:** The configurator catches the load error and aborts boot. Price never renders (DOM shows literal "— starting price"). Clicking a finish swatch wipes `aria-checked` from all radios. Add to Cart probably no-ops too. **Customers cannot configure a frame.**
- **Root cause:** File on disk is `/app/assets/sample/sample_poster.jpg` (underscore, in `assets/` not `static/`). Template + JS reference `/static/sample/sample-poster.jpg` (hyphen). Two mismatches: directory and filename separator.
- **Repro screenshots:** `01-preview-demo-desktop.png` (initial state), `02-preview-demo-after-clicks.png` (after Black click — radios all unchecked).

#### B2. Customer login route rejects every POST: "CSRF token is missing"
- **Repro:** `curl -X POST https://staging.fishingposter.com/account/login -d 'email=qa@example.com'` → `400 The CSRF token is missing.`
- **Page source:** `<form method="post" action="/account/login"><input type="hidden" name="next" value=""><label>...email...<input type="email" name="email">...</label>` — **no `csrf_token` hidden input is rendered.**
- **Impact:** `/account/login` POST cannot succeed. `customer_login_tokens` table has 0 rows. Outbox has zero `email.account_magic_link` entries. **The entire customer auth flow is broken** — magic links are never issued, never delivered.
- **Server log confirms:** repeated `The CSRF token is missing.` lines after each POST.
- **Fix scope:** Add `{{ csrf_token() }}` (Flask-WTF) or `<input name="csrf_token" value="{{ csrf_token() }}" type="hidden">` to `review_app/templates/account/login.html`. Admin login template already does this correctly — pattern can be copied.

#### B3. Audit log is broken — every admin action fails to write
- **Repro:** Login as admin, check `audit_log` table → 0 rows. Server logs show:
  ```
  audit.after_request failed: (psycopg.errors.UndefinedColumn) column "action" does not exist
  CONTEXT: PL/pgSQL function audit_log_search_vector_update() line 3 at assignment
  ```
- **Trigger source on prod DB:**
  ```sql
  NEW.search_vector := to_tsvector('simple', concat_ws(' ',
      COALESCE(action::text, ''), ' ',          -- BROKEN: needs NEW.action
      COALESCE(target_type::text, ''), ' ',     -- BROKEN: needs NEW.target_type
      COALESCE(target_id::text, '')));          -- BROKEN: needs NEW.target_id
  ```
- The trigger references unqualified column names. In a plpgsql `BEFORE INSERT` trigger you must use `NEW.action`, not `action`.
- **Impact:** `audit.after_request failed` on every admin login, every admin route view. The transaction is rolled back. Audit log is silently empty. This is a **compliance and forensics blocker** — admin actions are not auditable.
- **Fix:** Update `audit_log_search_vector_update()` definition: `NEW.action`, `NEW.target_type`, `NEW.target_id`. Most likely a recent migration regression (column rename/case change in the trigger source).

#### B4. Stripe and Prodigi credentials are missing on staging
- **Repro:** Visit `/admin/settings/integrations` (logged in as admin):
  - Stripe → **error**, "STRIPE_SECRET_KEY not set"
  - Prodigi → **error**, "PRODIGI_SANDBOX_API_KEY not set"
  - Recraft → error, "env var not set"
  - Resend → error "HTTP 401" *(false alarm — see W1)*
  - Smarty / OpenAI / Replicate / DO Spaces → healthy
- **Impact:** End-to-end checkout cannot work. End-to-end fulfillment (order to Prodigi) cannot work.
- **Fix:** Set staging env vars in Coolify: `STRIPE_SECRET_KEY` (test mode), `STRIPE_WEBHOOK_SECRET`, `PRODIGI_SANDBOX_API_KEY`, optionally `RECRAFT_API_KEY`. Then redeploy.

#### B5. Render pipeline still uses Phase-3 placeholder renderer
- **Repro:** `docker exec ... python -c 'from review_app.render import RenderSpec, get_or_render_tier; ...'` → 
  ```
  NotImplementedError: _default_master_renderer is a Phase 3 wiring placeholder.
      Pass a custom `master_renderer` to render_tier() until the route handlers
      are migrated. See docs/render-tiers.md.
  ```
- **Impact:** Any tier render call without an explicit `master_renderer=` argument throws. `ai_usage_log` table is empty (0 rows since boot, confirming no real renders have happened on staging).
- **Note:** This is documented behavior per `docs/render-tiers.md`. Whether it's a blocker depends on whether the route handlers in phase-6-polish were supposed to migrate to passing `master_renderer=`. If "ship to production" means real customers buy real prints, **this must be wired before launch**.

#### B6. `/cart` page renders the legacy "wildprint review" header instead of the storefront header
- **Repro:** `curl https://staging.fishingposter.com/cart` — `<title>Your cart · fishingposter.com</title>` but body shows:
  ```html
  <header class="site-header">
    <a class="site-title" href="/console">wildprint review</a>
    <nav class="site-nav">
      <a href="/console">Console</a>
      <a href="/browse">Browse</a>
      <a href="/create">Create Poster</a>
      <a href="/admin/">Admin</a>
      <form method="post" action="/copy-masters">
        <button>Copy Masters</button>
      </form>
    </nav>
  </header>
  ```
- The other storefront pages (`/preview/_demo`, `/account/login`) use the correct `storefront-header` with brand "fishingposter.com" and `/cart` + `/account/login` nav links.
- **Impact:** `/cart` shows internal admin nav (Console, Browse, Create Poster, Copy Masters) to public customers. Brand mismatch + leaks internal endpoints + the "Copy Masters" button is an internal action that probably 4xx's for unauthenticated visitors.
- **Fix:** Cart template should extend the storefront layout, not the original wildprint admin layout.

---

### Warnings (should fix soon)

#### W1. `Integrations` page shows Resend as "error / HTTP 401" but Resend is actually working
- The probe hits `GET /domains` which requires a full-access key. The configured `RESEND_API_KEY` is intentionally a "restricted_api_key" (send-only — best practice).
- Direct `POST /emails` test from inside the web container succeeded: id `8cb1ef87-250c-402f-a070-991d55450676`.
- **Fix:** Change the integrations probe to test `POST /emails` (with a no-op tag/test domain) or short-circuit to "healthy" when a `re_*` key is present and `/domains` returns the literal `restricted_api_key` body.

#### W2. Admin login form is rendering with Google Fonts inlined `<link>` redirected — adds ~200ms to TTI
- Both `admin/login` and storefront pages preconnect to fonts.googleapis.com and load Inter via stylesheet on every page. Cache is fine but a self-host or fontsource swap would help.
- Not a blocker.

#### W3. `worker` does not log "registered N jobs" at boot
- The QA spec said: *"The scheduler should log 'registered N jobs' at boot (5 jobs per Phase 5a)."*
- Worker (RQ) logs `worker.start { queues: ["high","default","low"] }` then `*** Listening on high, default, low...`. No "registered" line.
- Scheduler container is missing entirely (see deferred). The "registered N jobs" log was likely supposed to be from the scheduler.
- Worker round-trip itself is healthy (see Pass section).

#### W4. AI usage log is empty — never been written to on staging
- `SELECT count(*) FROM ai_usage_log` → 0.
- Likely correct (no renders run), but worth noting that this means we have not yet exercised the AI usage logging code path on staging at all. Logging rounds-trip cannot be confirmed until B5 unblocks real renders.

#### W5. Outbox has only 1 row total (2026-05-06, status=failed)
- Single `email.order_confirmed` row, status `failed`. No magic-link emails ever queued (because of B2).
- Recommend re-running once B2/B4 fixes ship: the outbox→Resend retry loop has not been exercised on staging beyond a single failed write.

#### W6. `/cart/add` POST is a 404; cart add must be via `/api/cart` (POST/PUT)
- Probe results: `POST /api/cart -> 405`, `POST /api/cart/add -> 400`, `POST /api/checkout/start -> 400`.
- The 400s suggest the routes exist but reject our payload (missing required body fields). End-to-end add-to-cart could not be confirmed without configurator working (B1) — recommend re-test after B1 fix.

#### W7. POST `/api/checkout/start` returns 400 (likely "missing customer / address / SKU")
- Couldn't confirm "clean error message" required by the QA spec because B1 prevented adding an item to cart, and B4 means Stripe key is missing anyway.
- After B1+B2+B4 are fixed, re-run the full add-to-cart → checkout flow.

---

### Pass (no action needed)

- HTTP smoke (anonymous): `/`, `/create`, `/preview/_demo`, `/preview/data/frame_skus.json`, `/cart`, `/account/login`, `/admin/login` all return 200.
- `/account/orders` without session → 302 redirect (correct).
- frame_skus.json contains 32 SKUs (4 sizes × 8 finishes) as Phase 6 spec requires.
- Admin login flow: email + password + CSRF works, lands on `/admin` with title "Admin · Dashboard · fishingposter.com".
- Admin sidebar: all 8 expected categories present (Dashboard, Orders, Customers, Catalog, Fulfillment, Content, Analytics, Settings).
- All 18 admin routes return 200 with sidebar markup intact (logged-in session).
- Frame SKUs admin page renders SKU rows (cf-12x16-black, cf-16x20-black, etc.).
- Configurator UI shape is correct: 8 finish swatches (Walnut/Black/White/Natural/Antique Silver/Antique Gold/Dark Grey/Light Grey), 4 sizes (12x16, 16x20, 18x24, 24x36), Add to Cart button present — the markup is right; only the JS boot is broken.
- Worker round-trip succeeded: `enqueue(ping_job, "qa-staging")` returned a job id, RQ worker logs:
  ```
  default: review_app.queue.jobs.ping_job('qa-staging') (0f4b70e7-c43a-4473-a218-781de11819ff)
  Successfully completed ... in 0:00:00.000850s
  Job OK
  ```
- Smarty integration probe: 1 candidate returned for "1 Apple Park Way" in 417 ms — healthy.
- DO Spaces probe: 1 key seen, 504 ms — healthy.
- OpenAI / Replicate env vars present.
- Resend send actually works (the integrations page is wrong — see W1).
- DNS: staging.fishingposter.com → 134.122.113.128 (correct droplet).

---

### Deferred

#### D1. Scheduler container is not running on staging
- `docker ps` shows only web + worker + redis + postgres for the `wildprint-staging` resource set. No container with `coolify.serviceName = wildprint-staging-scheduler` (or similar) exists.
- The user explicitly said: *"If the scheduler container isn't up yet (still building), report that as a separate 'deferred check' and don't fail QA on it."* — so reporting per instructions.
- Recommend: spin up the scheduler service in Coolify before production cutover, then re-verify the "registered N jobs" boot log (Phase 5a expected 5 jobs).

---

## Screenshots

All saved to:
`/Users/grant/claude-workspace/Wildlife/staging-qa-screenshots-20260505/`
(Mirrored at `~/.gstack/projects/GrantFinale-wildprint/staging-qa-screenshots-20260505/staging-qa-screenshots-20260505/` via symlink.)

| File | What it shows |
|---|---|
| `01-preview-demo-desktop.png` | Initial /preview/_demo state — configurator visually present, frame preview area is blank because of B1 |
| `02-preview-demo-after-clicks.png` | After clicking Black swatch — confirms broken JS state |
| `10-admin-dashboard.png` | Successful admin login → Dashboard with full sidebar |
| `11-admin-frame-skus.png` | Frame SKUs catalog page with rows |
| `12-admin-integrations.png` | Integrations probe page — Stripe/Prodigi/Recraft errors, Smarty/Spaces/OpenAI/Replicate healthy |
| `desktop_*.png` | 1440×900 viewport screenshots: preview, cart, login, admin |
| `mobile_*.png` | 375×812 viewport screenshots: preview, cart, login, admin |

---

## Repro recipes for blockers

### B1 — Configurator broken
```bash
curl -I https://staging.fishingposter.com/static/sample/sample-poster.jpg
# expect: HTTP/2 200
# actual: HTTP/2 404

# inside container
ssh root@134.122.113.128 \
  'docker exec vlweqt7q9wi7e43jtkg6zodn-134707612366 sh -c "find /app -name \"*sample*poster*\""'
# /app/assets/sample/sample_poster.jpg   <-- wrong path AND wrong name
```

### B2 — Customer login CSRF
```bash
curl -sk -c /tmp/cj -b /tmp/cj https://staging.fishingposter.com/account/login \
  | grep -E 'csrf_token|name="email"'
# returns only the email field — no csrf_token hidden input

curl -sk -X POST https://staging.fishingposter.com/account/login \
  --data 'email=qa@example.com'
# 400 Bad Request: The CSRF token is missing.
```

### B3 — Audit log trigger
```bash
ssh root@134.122.113.128 \
  'docker exec o630hdmppejmchbw7gn2qmn2 psql -U fishingposter_staging_app -d fishingposter_staging \
   -c "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname=\"audit_log_search_vector_update\";"'
# search for "COALESCE(action::text" — should be "COALESCE(NEW.action::text"
```

### B4 — Missing keys
- Visit `/admin/settings/integrations` after logging in. First column shows red "error" badges for Stripe / Prodigi / Recraft.

### B5 — Render placeholder
```bash
ssh root@134.122.113.128 'docker exec vlweqt7q9wi7e43jtkg6zodn-134707612366 \
  sh -c "cd /app && python -c \"from review_app.db import get_session; \
    from review_app.render import RenderSpec, get_or_render_tier, TIER_PREVIEW; \
    s = get_session(); spec = RenderSpec(lake=\\\"lake-tahoe\\\", species=[\\\"largemouth_bass\\\"], art_style=\\\"hooked\\\"); \
    print(get_or_render_tier(s, spec, TIER_PREVIEW))\""'
# NotImplementedError: _default_master_renderer is a Phase 3 wiring placeholder.
```

### B6 — Cart legacy header
```bash
curl -sk https://staging.fishingposter.com/cart | grep -E 'site-title|site-header|wildprint'
# shows old wildprint review header
```

---

## Magic-link approach (per QA spec)

The spec said: *"need to read the link from the outbox table since Resend in staging will actually send mail to benedictmt@gmail.com — instead, query the outbox row directly via SSH+psql to extract the token URL."*

Approach used (and documented for future runs):
```bash
# 1) Trigger magic link (after B2 is fixed)
curl -sk -c /tmp/cj -b /tmp/cj https://staging.fishingposter.com/account/login -o /tmp/login.html
csrf=$(grep -oE 'name="csrf_token" value="[^"]+"' /tmp/login.html | head -1 | sed 's/.*value="\([^"]*\)".*/\1/')
curl -sk -b /tmp/cj -c /tmp/cj -X POST https://staging.fishingposter.com/account/login \
  --data-urlencode "csrf_token=$csrf" --data-urlencode "email=qa-test+$(date +%s)@fishingposter.com"

# 2) Query outbox payload (NOT the customer_login_tokens.token_hash — only the outbox payload has the unhashed link)
ssh root@134.122.113.128 \
  'docker exec o630hdmppejmchbw7gn2qmn2 psql -U fishingposter_staging_app -d fishingposter_staging \
   -c "SELECT payload FROM outbox WHERE kind=\"email.account_magic_link\" ORDER BY created_at DESC LIMIT 1;"'

# 3) Visit the magic_link_url from the JSON payload
```

This was **not exercisable in this QA run** because B2 prevents the POST from issuing any token (table `customer_login_tokens` is empty, outbox has zero magic-link rows).

---

## What we couldn't test (blocked by other findings)

| Test | Blocked by | What's needed |
|---|---|---|
| End-to-end magic link login | B2 | Add `csrf_token` to template |
| Add to cart from configurator | B1 | Fix sample-poster.jpg path |
| `/api/cart` POST with valid payload | B1 (no add-to-cart UI), W6 | Inspect cart API spec |
| `/api/checkout/start` clean error or success | B1, B2, B4 | Need cart + auth + Stripe key |
| AI usage log round-trip | B5 | Need real renderer wired |
| Render tier-1 round-trip | B5 | Wire master_renderer in route handlers |
| Scheduler "registered N jobs" boot log | D1 | Start scheduler container |

---

## Changelog of QA actions

1. Identified containers via `docker ps` + Coolify resource labels.
2. HTTP smoke 12 anonymous routes (Python urllib + curl).
3. `browse` interactive: visited /preview/_demo, snapshot configurator, attempted swatch/size click, captured console errors.
4. `browse` interactive: admin login (CSRF + email + password), confirmed dashboard + sidebar, walked all 18 admin routes in one session.
5. SSH+docker introspection: env vars, static asset filesystem layout, render module API, db tables, audit_log trigger source.
6. Worker round-trip: `enqueue(ping_job, ...)` from web container — confirmed picked up + completed by worker in <1ms.
7. Render attempt: confirmed `NotImplementedError` placeholder.
8. Resend probe: direct `POST /emails` from web container — succeeded (id returned).
9. Visual screenshots: 1440×900 + 375×812 for /preview/_demo, /cart, /account/login, /admin.
10. Cart route discovery via HTTP probe matrix.

— end of report —
