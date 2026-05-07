# Production Cutover Runbook

**Status:** DRAFT — do not execute. This runbook documents the steps Grant must approve + take to flip fishingposter.com from the legacy $49 unlock app to the new physical-poster commerce flow.

**Date drafted:** 2026-05-07
**Branch state:** all phase branches (phase-0-foundation through phase-6-polish) on GitHub, CI green
**Staging state:** updated when ready (see Step 1)

---

## What's actually changing on production

| Surface | Before cutover | After cutover |
|---|---|---|
| `wildlife.5story.com` | Active production app (Coolify `szncf7ts5gwk1idp2ubkgnvw`, branch `main`, $49 unlock + render flow) | Same Coolify app, **branch points at `main` after the merge** — gets all Phase 0-6 features. Existing $49 unlock continues working. |
| `fishingposter.com` | Resolves to same droplet, served by same Coolify app (DNS A → 134.122.113.128) | Same. The brand transition (wildlife.5story.com → fishingposter.com) is a separate decision; either keep both pointing at the app or 301 redirect wildlife.5story.com → fishingposter.com later. |
| `staging.fishingposter.com` | Coolify `vlweqt7q9wi7e43jtkg6zodn`, branch `phase-0-foundation` | Branch flipped to `phase-6-polish` for full validation; stays on staging branch indefinitely as a sandbox. |
| Postgres `fishingposter` (prod DB on benedict-ventures container) | Empty (no schema yet on prod side; Phase 0 created the DB but never ran migrations there) | After cutover, `alembic upgrade head` runs on container start (Phase 0 added this to Dockerfile CMD). All 25 migrations apply. The legacy `metadata/leads.json` data is migrated by 0013_migrate_leads_json into customers + synthesized $49 orders. |
| Stripe | One product (the $49 unlock) | Add the 32 physical-poster price line items dynamically per cart (already implemented; uses the existing Stripe account). |
| Prodigi | No prod orders yet (sandbox only via test orders) | Real orders start firing as customers buy. |
| RQ worker | None (was missing before Phase 0) | Coolify service `wildprint-worker` runs `Dockerfile.worker`. |
| Cron scheduler | None | Coolify service `wildprint-scheduler` runs `Dockerfile.scheduler`. **Replicas: 1** (rq-scheduler has no leader election). |
| Resend domain | Verified for `fishingposter.com` (DNS records live) | Same. |

---

## Pre-cutover checklist (Grant must clear ALL before proceeding)

- [ ] **Stripe Tax decision:** Is automatic_tax enabled with proper jurisdictional registration in your Stripe dashboard? If yes → set `STRIPE_TAX_ENABLED=true` in production. If no → leave unset; physical orders ship without sales tax until registered. Talk to your accountant if unsure — collecting sales tax in jurisdictions where you're not registered is a legal mess.
- [ ] **Stripe webhook v2:** Create a NEW webhook endpoint in your Stripe dashboard pointing at `https://fishingposter.com/webhook/stripe/v2` (and a separate one for staging). Subscribe to: `checkout.session.completed`, `payment_intent.succeeded`, `payment_intent.payment_failed`, `charge.refunded`. Copy the `whsec_...` signing secret into the production Coolify env as `STRIPE_WEBHOOK_SECRET_V2`. The legacy `/webhook/stripe` endpoint stays for the $49 unlock.
- [ ] **Brand transition decision:** Are we going live as `fishingposter.com` (with `wildlife.5story.com` redirected later), keeping both domains pointing at the same app, or something else? Affects FQDN list on the prod Coolify app.
- [ ] **Customer comms:** If the public URL is changing, prepared outreach to existing $49 customers (~N customers — count them in `metadata/leads.json`) explaining the new physical product line.
- [ ] **Prodigi production key:** The live key from `dashboard.prodigi.com` is already in `backups/prodigi-credentials.txt`. Confirm `PRODIGI_ENV=live` is set in production env (NOT sandbox). Also confirm the live webhook URL in Prodigi's dashboard points at `https://fishingposter.com/webhook/prodigi`.
- [ ] **Daily Postgres backup verified:** `/usr/local/bin/backup-fishingposter.sh` cron is running on the droplet. Verify `/var/backups/fishingposter/` has a recent file. Don't proceed if backups are silently failing.
- [ ] **Production env vars audit:** Pull the current production Coolify env vars, diff against the staging env vars file (`backups/staging-env-vars-final.txt`). Add the new ones (Phase 5 introduced ~15 new env vars — `STRIPE_TAX_ENABLED`, `STRIPE_WEBHOOK_SECRET_V2`, `STRIPE_CUSTOMER_AUTH_JWT_SECRET`, `RATE_LIMIT_*`, etc.).
- [ ] **Sentry account:** Optional but recommended. If you don't have one yet, sign up at sentry.io, create a project, drop the DSN into `SENTRY_DSN`. Without this, errors only surface in container logs.

---

## Cutover sequence

### Step 1 — Validate staging (do NOT skip)
1. Roll the staging Coolify app (`vlweqt7q9wi7e43jtkg6zodn`) to branch `phase-6-polish`.
2. Wait for build + healthcheck.
3. Add a new Coolify service `wildprint-staging-scheduler` using `Dockerfile.scheduler`, Replicas=1, same env vars.
4. Run end-to-end QA via `/qa` skill against `https://staging.fishingposter.com` — every customer flow + every admin flow.
5. Place a real test order against the Prodigi SANDBOX from staging, confirm the full chain works: Stripe Checkout → webhook v2 → outbox → Prodigi sandbox order created → tier-3 render queued → email lands in benedictmt@gmail.com → `/account/orders/<id>` shows the order.
6. **STOP if any step above fails.** Fix on a new branch, re-merge.

### Step 2 — Merge phases to main
Once staging is fully validated:
```bash
git checkout main
git merge --no-ff phase-6-polish -m "merge: ship Phase 0-6 (Prodigi integration + commerce + admin refactor + polish)"
git push origin main
```
Open a PR for review if you want extra eyes (recommended). Skim the diff: roughly +25,000 lines added across ~150 files.

### Step 3 — Flip production
1. The production Coolify app `szncf7ts5gwk1idp2ubkgnvw` is currently on branch `main`. After the merge in Step 2, the next deploy will pick up everything.
2. **Add the new production env vars** (the diff from Step 0's audit).
3. **Add a Redis service** to the production app if not already present (Phase 0.4 needs it for the queue).
4. **Add the worker service** (`wildprint-worker` running `Dockerfile.worker`) — same env vars.
5. **Add the scheduler service** (`wildprint-scheduler` running `Dockerfile.scheduler`, Replicas=1).
6. **Trigger a deploy of the main app.**
7. Watch the build logs. The container will run `alembic upgrade head` on start, applying all 25 migrations to the prod DB. The `0013_migrate_leads_json` migration will copy any existing leads.json data into the customers table (idempotent — safe even if it's already been run on staging).
8. Wait for healthcheck.

### Step 4 — Smoke production
Same QA flow as Step 1, but against production. Use a real test card (Stripe test mode with the production key — see Stripe docs) OR place a real $1 test order yourself and refund it.

### Step 5 — Watch
Sentry, Coolify logs, the admin Fulfillment > Webhooks page. First 24 hours are the critical window. Failed orders should fire Sentry alerts (Phase 5 wired this).

---

## Rollback plan

If anything is broken after Step 3:
1. In Coolify, change the production app's branch back to its prior commit SHA (Coolify keeps deployment history). Roll back to the last known-good commit on `main` BEFORE the Phase merge.
2. The `alembic upgrade head` is forward-only on container start. **The new migrations are reversible** but auto-rollback isn't wired. To roll back schema, exec into the rolled-back container and run `alembic downgrade <previous_revision>`. Most Phase 0-6 changes are additive (new tables, new columns); rolling back forward-compat code without rolling back the schema is usually safe for 24 hours.
3. The legacy $49 unlock flow uses `metadata/leads.json` (untouched throughout) and the legacy `/webhook/stripe` endpoint — these continue working even with new code in place. So even mid-cutover, $49 customers aren't blocked.

---

## What doesn't roll back

- Stripe webhooks once delivered are not un-delivered.
- Prodigi orders once placed cannot be unplaced (the cancel window is short — see `project_prodigi_quirks.md`).
- Resend emails once sent cannot be unsent.

These are normal real-world consequences of accepting payments. The system is idempotent on retry, but external side-effects are real.

---

## Sign-off

I (the Commander) won't execute Step 3 (production flip) without an explicit go from Grant. Steps 1 and 2 can be done autonomously since they're staging-only / git operations. The production flip is yours to authorize.

When ready, reply with: `cutover go` and I'll execute Step 3 immediately, watching the deploy in real-time and standing ready to rollback if anything goes sideways.
