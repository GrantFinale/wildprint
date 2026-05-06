# Phase 0 — Foundation: Sub-Task Breakdown

**Branch:** `phase-0-foundation` · **Production stays live throughout** · No prod deploys until staging-validated.

---

## Sub-tasks

### 0.1 — Provision Postgres database
- **Goal:** A fresh `fishingposter` Postgres 16 database exists on the `benedict-ventures` droplet container (port 5433) with a dedicated app role and connection string stored in Coolify secrets.
- **Acceptance criteria:**
  - `CREATE DATABASE fishingposter` executed against `o630hdmppejmchbw7gn2qmn2` container
  - App role `fishingposter_app` with least-privilege grants (CRUD only, no superuser)
  - `DATABASE_URL` in dev `.env`, staging Coolify, prod Coolify (prod unused for now)
  - `psql "$DATABASE_URL" -c '\dt'` from local + droplet returns empty list
  - Daily `pg_dump` cron added on droplet writing to `/var/backups/fishingposter/`
- **Dependencies:** none — can start immediately
- **Effort:** 2 h
- **Files:** `wildprint/docs/db-bootstrap.sql` (new); `wildprint/scripts/backup-db.sh` (new)
- **Env vars:** `DATABASE_URL` (all envs)
- **Risk:** Existing container shares port 5433 with other apps; verify no name collision and that pg_hba allows the app user from Coolify network only.

### 0.2 — SQLAlchemy 2.0 + Alembic scaffold
- **Goal:** Typed SQLAlchemy 2.0 declarative base wired into Flask, Alembic configured, baseline empty migration applied.
- **Acceptance criteria:**
  - `review_app/db/__init__.py` exposes `engine`, `SessionLocal`, `Base`
  - `alembic/` directory with `env.py` reading `DATABASE_URL`, autogenerate working
  - `alembic upgrade head` runs clean against fresh DB and is idempotent
  - Flask app context manages session per-request (`scoped_session` + teardown)
  - Pytest fixture `db_session` rolls back per test
- **Dependencies:** 0.1
- **Effort:** 4 h
- **Files:** `wildprint/review_app/db/__init__.py`, `wildprint/review_app/db/base.py`, `wildprint/alembic.ini`, `wildprint/alembic/env.py`, `wildprint/alembic/versions/0001_baseline.py`, `wildprint/requirements.txt` (add `sqlalchemy>=2.0`, `alembic`, `psycopg[binary]`)
- **Env vars:** none new
- **Risk:** Coexistence with Flask's existing app factory pattern; teardown ordering with the existing render-job thread pool.

### 0.3 — Cloudflare R2 bucket + signed-URL helper
- **Goal:** Two R2 buckets (`fishingposter-public`, `fishingposter-private`) provisioned with API tokens; Python helper module returns signed PUT/GET URLs.
- **Acceptance criteria:**
  - Buckets exist in Cloudflare dashboard with lifecycle rule (private: 18 mo retention)
  - `review_app/storage/r2.py` exposes `put_object`, `get_signed_url`, `delete_object` typed
  - Round-trip integration test uploads a 1 MB blob, fetches via signed URL, deletes
  - CORS configured on public bucket for `https://fishingposter.com` and staging origin
  - Custom domain `cdn.fishingposter.com` CNAME points at public bucket (DNS via doctl)
- **Dependencies:** none — can start immediately (parallel to 0.1)
- **Effort:** 5 h
- **Files:** `wildprint/review_app/storage/r2.py`, `wildprint/review_app/storage/__init__.py`, `wildprint/tests/storage/test_r2.py`, `wildprint/requirements.txt` (add `boto3`)
- **Env vars:** `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_PUBLIC_BUCKET`, `R2_PRIVATE_BUCKET`, `R2_PUBLIC_BASE_URL` (all envs; dev uses a separate `*-dev` bucket)
- **Risk:** R2 signed-URL semantics differ subtly from S3 (path-style vs virtual-host); test against real R2, not moto.

### 0.4 — Redis + RQ render queue
- **Goal:** Redis container running on droplet, RQ worker process managed by Coolify, scaffold job that no-ops proves end-to-end enqueue → execute.
- **Acceptance criteria:**
  - Redis container on droplet (or reuse existing if Coolify provides one)
  - `review_app/queue/__init__.py` exposes `enqueue(job_fn, *args)` typed
  - Worker entry point `python -m review_app.queue.worker` runs in its own Coolify service
  - Test job `ping_job()` enqueued from Flask, executed by worker, result persisted in Redis
  - Failed jobs land in failed queue with structured traceback
- **Dependencies:** none — can start immediately (parallel)
- **Effort:** 5 h
- **Files:** `wildprint/review_app/queue/__init__.py`, `wildprint/review_app/queue/worker.py`, `wildprint/review_app/queue/jobs.py`, `wildprint/Dockerfile.worker` (new), `wildprint/requirements.txt` (add `rq`, `redis`)
- **Env vars:** `REDIS_URL` (all envs)
- **Risk:** Coolify rolling deploys for the worker need a graceful shutdown signal so in-flight jobs aren't killed mid-render.

### 0.5 — Resend account + DNS records
- **Goal:** Resend account active for `fishingposter.com`, SPF/DKIM/DMARC records published, test transactional email sends successfully.
- **Acceptance criteria:**
  - Resend domain `fishingposter.com` shows "Verified" status
  - SPF record: `v=spf1 include:_spf.resend.com ~all`
  - DKIM CNAMEs from Resend dashboard added via `doctl compute domain records create`
  - DMARC: `v=DMARC1; p=quarantine; rua=mailto:dmarc@fishingposter.com`
  - `review_app/email/resend_client.py` sends a test email; assert 200 + delivery log in Resend
- **Dependencies:** Grant pre-flight (Resend account creation)
- **Effort:** 3 h
- **Files:** `wildprint/review_app/email/__init__.py`, `wildprint/review_app/email/resend_client.py`, `wildprint/tests/email/test_resend_smoke.py`, `wildprint/requirements.txt` (add `resend`)
- **Env vars:** `RESEND_API_KEY` (all), `EMAIL_FROM` (all; dev uses sandbox)
- **Risk:** DNS propagation can take hours; Resend domain verification may require a re-check loop.

### 0.6 — Admin auth (Flask-Login + Argon2) + RBAC
- **Goal:** Admin routes gated behind login with `admin/staff/viewer` roles. Existing unprotected `/admin*` pages now require `admin` role.
- **Acceptance criteria:**
  - `users` table (id, email, password_hash, role enum, created_at)
  - `/admin/login` + `/admin/logout` routes; Argon2 hashing
  - `@requires_role('admin')` decorator wraps every existing admin route
  - Bootstrap CLI: `flask create-admin <email>` prompts for password
  - Pytest covers: anon redirect, viewer 403 on admin route, admin 200, logout invalidates session
  - Existing $49 unlock cookie flow untouched (separate session key)
- **Dependencies:** 0.2
- **Effort:** 6 h
- **Files:** `wildprint/review_app/auth/__init__.py`, `wildprint/review_app/auth/models.py`, `wildprint/review_app/auth/decorators.py`, `wildprint/review_app/templates/admin/login.html`, `wildprint/review_app/cli.py`, `wildprint/alembic/versions/0002_users.py`
- **Env vars:** `SECRET_KEY` (already exists; verify rotated for new cookie scope), `ADMIN_BOOTSTRAP_EMAIL` (dev only)
- **Risk:** Don't lock yourself out of prod admin during the cutover — deploy auth as opt-in via env flag, flip after bootstrap user exists.

### 0.7 — Pytest + pytest-flask + coverage harness
- **Goal:** `pytest` runs locally and in CI with Flask app context, DB rollback fixtures, and minimum 60% coverage gate on new modules.
- **Acceptance criteria:**
  - `pytest.ini` / `pyproject.toml` configured; `conftest.py` provides `app`, `client`, `db_session`
  - Tests for 0.2, 0.3, 0.5, 0.6 all pass locally
  - Coverage report excludes `poster_layout/` (renderer is untouched)
  - `pytest -m "not integration"` runs in <30 s
- **Dependencies:** 0.2
- **Effort:** 4 h
- **Files:** `wildprint/pyproject.toml`, `wildprint/tests/conftest.py`, `wildprint/tests/__init__.py`, `wildprint/requirements-dev.txt` (new)
- **Env vars:** `TESTING=1` (CI)
- **Risk:** Existing `app.py` is monolithic; introducing `create_app()` factory may surface import-time side effects.

### 0.8 — GitHub Actions CI (lint, mypy strict, pytest, build)
- **Goal:** Every push/PR runs ruff, mypy strict on `review_app/`, pytest, and a Docker build. Branch protection blocks merge on red CI.
- **Acceptance criteria:**
  - `.github/workflows/ci.yml` runs on `push` + `pull_request`
  - Jobs: `lint` (ruff), `typecheck` (mypy strict on `review_app/` + `commerce/`), `test` (pytest with Postgres + Redis service containers), `build` (docker build)
  - mypy strict passes for all new modules; legacy `app.py` excluded explicitly
  - CI green on `phase-0-foundation` branch before merge to `main`
- **Dependencies:** 0.7
- **Effort:** 4 h
- **Files:** `.github/workflows/ci.yml`, `wildprint/mypy.ini`, `wildprint/ruff.toml`
- **Env vars:** GitHub Actions secrets: `PRODIGI_SANDBOX_KEY` (placeholder), test `DATABASE_URL` from service container
- **Risk:** Mypy strict on Flask/SQLAlchemy code requires careful stub setup; budget extra hour for false positives.

### 0.9 — Sentry + structlog
- **Goal:** Structured JSON logs for new code paths, Sentry capturing exceptions in dev/staging/prod with environment + release tags.
- **Acceptance criteria:**
  - `structlog` configured with JSON renderer in non-dev, console renderer in dev
  - Request ID + user ID bound to log context per request
  - Sentry SDK initialized; test exception in `/admin/_sentry_test` reaches Sentry dashboard
  - PII scrubber on Sentry (email, IP) before send
  - Existing `print()` and `app.logger` calls left alone (legacy code)
- **Dependencies:** 0.2
- **Effort:** 3 h
- **Files:** `wildprint/review_app/observability/__init__.py`, `wildprint/review_app/observability/logging.py`, `wildprint/review_app/observability/sentry.py`, `wildprint/requirements.txt` (add `structlog`, `sentry-sdk[flask]`)
- **Env vars:** `SENTRY_DSN` (staging + prod), `SENTRY_ENVIRONMENT`, `LOG_LEVEL`
- **Risk:** Sentry release tagging needs git SHA injected at Docker build; thread through Coolify build args.

### 0.10 — AI usage logging table + interceptor
- **Goal:** Every OpenAI / Recraft / Replicate call records provider, model, input units, output units, computed cost USD, user_id, job_id, latency, status.
- **Acceptance criteria:**
  - `ai_usage` table migrated (provider, model, units_in, units_out, cost_cents, user_id nullable, job_id nullable, status, latency_ms, created_at, request_hash)
  - Wrapper modules `review_app/ai/openai_client.py`, `recraft_client.py`, `replicate_client.py` — every existing call site routes through these
  - Cost computation table per provider/model in `review_app/ai/pricing.py` with last-updated date
  - Failed calls still logged with `status='error'` and exception class
  - Pytest mocks provider responses, asserts row inserted with correct cost
- **Dependencies:** 0.2
- **Effort:** 6 h
- **Files:** `wildprint/review_app/ai/__init__.py`, `wildprint/review_app/ai/openai_client.py`, `wildprint/review_app/ai/recraft_client.py`, `wildprint/review_app/ai/replicate_client.py`, `wildprint/review_app/ai/pricing.py`, `wildprint/alembic/versions/0003_ai_usage.py`; modify existing call sites in `providers/` and `review_app/app.py`
- **Env vars:** none new (reuse existing AI keys)
- **Risk:** Touching every existing AI call site risks breaking the live $49 unlock flow; gate with feature flag `AI_LOGGING_ENABLED` and ship logging in shadow mode first.

### 0.11 — Staging Coolify app
- **Goal:** Second Coolify app `fishingposter-staging` deployed from `phase-0-foundation` branch, pointing at staging DB, Prodigi sandbox env, and dev R2 buckets.
- **Acceptance criteria:**
  - DNS: `staging.fishingposter.com` (or `fishingposter-staging.5story.com`) resolves to droplet
  - Coolify app builds from branch `phase-0-foundation` on push
  - Separate Postgres database `fishingposter_staging` on same container
  - Health check endpoint `/healthz` returns 200 with DB + Redis + R2 connectivity
  - Basic-auth gate on staging (separate from admin auth) so it isn't indexed
- **Dependencies:** 0.1, 0.4, 0.5, 0.8
- **Effort:** 4 h
- **Files:** `wildprint/review_app/healthz.py`, Coolify config (no repo files)
- **Env vars:** Full staging env in Coolify (all of the above keys with staging values)
- **Risk:** Coolify env-var sprawl across two apps; document the canonical list in `wildprint/docs/envs.md`.

### 0.12 — Phase 0 acceptance smoke + handoff doc
- **Goal:** End-to-end smoke test on staging proving all foundations work together; short doc declaring Phase 0 done and Phase 1 unblocked.
- **Acceptance criteria:**
  - Smoke script: log in as admin → enqueue render job → upload to R2 → send email → log AI call → verify all rows + Sentry breadcrumb
  - All CI green for 3 consecutive commits
  - Phase 0 retro doc lists deltas vs estimates
- **Dependencies:** all prior
- **Effort:** 3 h
- **Files:** `wildprint/scripts/smoke_phase0.py`, `wildprint/docs/phase-0-done.md`
- **Env vars:** none
- **Risk:** Smoke script becomes the de facto integration test — keep it in CI to prevent regression.

**Total effort:** 49 person-hours.

---

## 1. Dependency DAG

```
[0.1 Postgres] ──► [0.2 SQLA/Alembic] ──┬──► [0.6 Auth]
                                        ├──► [0.7 Pytest] ──► [0.8 CI] ──┐
                                        ├──► [0.9 Sentry/log]            │
                                        └──► [0.10 AI logging]           │
                                                                         ▼
[0.3 R2]              ──────────────────────────────────────────► [0.11 Staging] ──► [0.12 Smoke]
[0.4 Redis/RQ]        ──────────────────────────────────────────►       ▲
[0.5 Resend+DNS]      ──────────────────────────────────────────────────┘
```

**Critical path:** 0.1 → 0.2 → 0.7 → 0.8 → 0.11 → 0.12 = 2 + 4 + 4 + 4 + 4 + 3 = **21 h**.

---

## 2. Parallelizable workstreams

- **Stream A — DB & app spine:** 0.1 → 0.2 → 0.6 / 0.9 / 0.10 (15 h)
- **Stream B — Object & queue infra:** 0.3 ‖ 0.4 (10 h, both independent)
- **Stream C — Email + DNS:** 0.5 (3 h, independent; DNS-bound, start day 1)
- **Stream D — Test/CI/staging:** 0.7 → 0.8 → 0.11 → 0.12 (after Streams A+B+C land) (15 h)

Streams A, B, C run fully concurrent. Stream D is the integration funnel.

---

## 3. Order of operations (single engineer, sequential)

1. 0.1 Postgres (kick off DNS for 0.5 in parallel — propagation timer)
2. 0.5 Resend records (background while DNS propagates)
3. 0.2 SQLAlchemy + Alembic
4. 0.3 R2 (independent, do here to amortize cloud-console context switch)
5. 0.4 Redis + RQ
6. 0.6 Admin auth
7. 0.10 AI usage logging
8. 0.9 Sentry + structlog
9. 0.7 Pytest harness (backfill tests for 0.2–0.6 as needed)
10. 0.8 GitHub Actions CI
11. 0.11 Staging Coolify app
12. 0.12 Smoke + handoff

---

## 4. Calendar timeline (1 eng, 6 h/day, 5 d/wk)

49 h ÷ 6 h/day = **~8.5 productive days ≈ 2 calendar weeks** including buffer for DNS propagation, Coolify quirks, and one bad-day of debugging. Original plan estimate (6–8 days) was optimistic by ~30%; this breakdown is the honest number.

---

## 5. Pre-flight checklist (Grant only)

- [ ] Resend account created at resend.com, billing email confirmed
- [ ] Cloudflare account active; R2 enabled (requires payment method even on free tier)
- [ ] GitHub repo `GrantFinale/wildprint` admin access confirmed for adding Actions secrets
- [ ] Decision: staging hostname — `staging.fishingposter.com` or `fishingposter-staging.5story.com`?
- [ ] Decision: Sentry org/project (free tier OK for Phase 0)
- [ ] Confirm Postgres container `o630hdmppejmchbw7gn2qmn2` has spare capacity for two more DBs (~50 MB Phase 0)
- [ ] Backup `metadata/leads.json` and `output/` off-droplet before any Phase 0 work touches the box
- [ ] Snapshot Coolify config for the live `wildlife.5story.com` app (rollback safety)

---

## 6. Definition of done — Phase 0 → Phase 1 unblocked

Phase 0 is complete when **all** of:

1. CI green on `phase-0-foundation` for 3 consecutive commits (lint, mypy strict, pytest, build).
2. `staging.fishingposter.com` (or chosen host) responds 200 on `/healthz` with DB + Redis + R2 + Resend connectivity proven.
3. Admin login works on staging; existing `/admin*` pages gated; existing $49 unlock flow on prod **still works** (verified by manual checkout).
4. Smoke script `scripts/smoke_phase0.py` passes against staging.
5. AI usage logging captures a real OpenAI call end-to-end with cost row.
6. Daily DB backup cron runs once successfully on droplet.
7. `phase-0-done.md` written and committed.

Phase 1 (Prodigi client) starts the moment all 7 are checked.

---

## 7. Top 3 Phase 0 risks

1. **Touching live AI call sites breaks the $49 flow.** Mitigation: ship 0.10 behind `AI_LOGGING_ENABLED=false` first; flip on staging only; add a regression test that mocks Stripe checkout end-to-end before flipping prod.
2. **Coolify rolling deploys of the new RQ worker drop in-flight jobs.** Mitigation: implement SIGTERM handler that finishes the current job (max 5 min) before exit; configure Coolify health-check + grace period accordingly. Phase 0 only enqueues no-op test jobs, so blast radius is tiny — but lock the pattern in now.
3. **DNS / Resend verification stalls and blocks Stream C.** Mitigation: kick DNS records off on day 1 even before code; use Resend's sandbox domain for dev tests so engineering work isn't gated on external propagation.
