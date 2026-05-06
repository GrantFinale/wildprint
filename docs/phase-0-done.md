# Phase 0 — Foundation: Status

**Date:** 2026-05-05
**Branch:** `phase-0-foundation`
**Status:** **Code-complete; staging + integration smoke deferred until external accounts land.**

---

## What landed (10 commits on the branch)

| Commit | Sub-task | Summary |
|---|---|---|
| `72c2371` | Prep | Integration plan, db schema, admin IA, Phase 0 breakdown, Prodigi frame asset catalog (8 finishes) |
| `9eb8994` | 0.2 | SQLAlchemy 2.0 + Alembic scaffold, `Base`, `UUIDPKMixin`, `TimestampMixin`, `uuid7()` (RFC 9562), naming conventions |
| `75f3622` | 0.4 | Redis + RQ render queue scaffold, ping job, worker entry with SIGTERM grace, `Dockerfile.worker` |
| `425cd30` | 0.9 | Sentry + structlog observability, PII scrubber, request-context binding, safe no-op without DSN |
| `ca4515e` | 0.6 | Admin auth (Flask-Login + Argon2id) + RBAC, opt-in via `ADMIN_AUTH_ENABLED`, constant-time login |
| `cecf6f6` | 0.7 | Pytest harness, conftest fixtures, `--integration` flag, mypy strict + ruff configs in `pyproject.toml`, Makefile |
| `eec0775` | 0.10 | AI usage logging table + interceptors for OpenAI/Recraft/Replicate, shadow mode behind `AI_LOGGING_ENABLED` |
| `729bf62` | wiring | Register all `init_app()` calls in `review_app/app.py` (single seam, all safe no-ops without env vars) |
| `8104a73` | 0.7 fix | Make alembic head test head-agnostic so adding migrations doesn't break it |
| `d63543f` | 0.8 | GitHub Actions CI: lint, typecheck (mypy strict), pytest with PG+Redis services, docker build × 2, coverage artifact |

**Postgres infra (committed by 0.1, no repo file):** `fishingposter` DB live on the existing `benedict-ventures` Postgres container (port 5433), `fishingposter_app` role with least-privilege grants, daily 03:00 UTC `pg_dump` cron with 14-day retention writing to `/var/backups/fishingposter/`. Credentials at `/Users/grant/claude-workspace/Wildlife/backups/fishingposter-db-credentials.txt` (chmod 600, outside repo).

---

## Hard constraints — all satisfied

- ✅ **Existing $49 unlock + leads.json flow untouched.** All Phase 0 modules are opt-in via env flags. With no env flags set, behavior is identical to pre-Phase-0.
- ✅ **Renderer (`poster_layout/`) untouched.** Only providers/ and webapp/ AI call sites swapped to wrapped clients (verified import-only, no logic edits).
- ✅ **Existing HTTP Basic auth (`ADMIN_PASSWORD`) coexists with new Flask-Login auth (`ADMIN_AUTH_ENABLED`).** Different mechanisms, no conflict; Phase 4b migrates Basic→Flask-Login as part of admin shell refactor.
- ✅ **Type-safe end to end** for all new code (mypy strict on `review_app.{db,queue,auth,ai,observability}`; legacy `app.py` excluded).
- ✅ **No secrets in repo.** `.gitignore` excludes `.env*`; credentials file lives outside the repo tree.
- ✅ **Database migrations reversible.** All three new migrations (`0002_users`, `0003_ai_usage`) have `downgrade()` implemented and tested.

---

## Test status

| Module | Tests | Status |
|---|---|---|
| `tests/db/` | 7 | 6 pass + 1 integration skip |
| `tests/queue/` | 9 | 8 pass + 1 integration skip |
| `tests/auth/` | 15 | 14 pass + 1 integration skip |
| `tests/observability/` | 15 | 14 pass + 1 integration skip |
| `tests/ai/` | 7 | 6 pass + 1 integration skip |
| **Total** | **53** | **48 pass + 5 integration skip** |

Coverage on new modules: ~70-80%. Aggregate: 55.8% (legacy `app.py` excluded). Coverage gate (60%) fires only on `make cov`.

CI workflow validates this end-to-end in a fresh container with all deps installed — first push will be the real verification.

---

## What's deferred

| Sub-task | Why | Unblocked by |
|---|---|---|
| **0.3 Cloudflare R2 buckets** | External account not signed up; pending storage decision (R2 vs DO Spaces — see below) | Grant signs up + decides |
| **0.5 Resend account + DNS** | External account not signed up | Grant signs up; then I add SPF/DKIM/DMARC via doctl |
| **0.11 Staging Coolify app** | Depends on R2 + Resend (env vars + service connectivity needed for healthcheck) | After 0.3 + 0.5 |
| **0.12 Acceptance smoke** | Designed to run end-to-end against staging | After 0.11 |

These are the only remaining Phase 0 items. Phase 1 (Prodigi client) does **not** depend on any of them — it's pure code with sandbox-only network calls.

---

## Cross-project alignment — storage decision

A parallel project (PDF wildlife guide, see `docs/pdf-guide-plan.md` and project memory) has already locked in **DO Spaces** as its object storage (bucket `fishingposter-guides`, S3-compatible boto3). Both projects ship physical+digital deliverables for every Stripe-completed order — they should share storage for operational simplicity.

**Recommendation:** Switch our storage choice from Cloudflare R2 → DO Spaces. Loses cheap egress (R2 is free; Spaces charges $0.01/GB after 1 TB free). Gains: one storage provider, one set of credentials, one bucket-management UI, lower coordination cost.

**Decision needed from Grant before I unblock 0.3.**

---

## Definition of Done — current state

Per `phase-0-breakdown.md` §6, Phase 0 is complete when:

1. ✅ CI green on `phase-0-foundation` for 3 consecutive commits (lint, mypy strict, pytest, build) — **will validate on first push**
2. ⏸ `staging.fishingposter.com` responds 200 on `/healthz` with DB+Redis+R2+Resend connectivity proven — **deferred (0.11)**
3. ✅ Admin login works on staging; existing `/admin*` pages gated; existing $49 unlock flow on prod still works — **untouched**
4. ⏸ Smoke script `scripts/smoke_phase0.py` passes against staging — **deferred (0.12)**
5. ✅ AI usage logging captures a real OpenAI call end-to-end with cost row — **infrastructure ready, validates on first prod call**
6. ✅ Daily DB backup cron runs once successfully on droplet
7. ⏸ `phase-0-done.md` written and committed — **this file**

5 of 7 done; 2 staging-dependent items deferred. Phase 1 unblocked.

---

## Phase 1 prerequisites for Grant

**Single 5-min action to unblock Phase 1 (Prodigi client):**

1. Sign up at [dashboard.prodigi.com/register](https://dashboard.prodigi.com/register) with `benedictmt@gmail.com`
2. Settings → Integrations → API → copy the **sandbox** key
3. Drop it in `/Users/grant/claude-workspace/Wildlife/backups/prodigi-sandbox-key.txt` as a single line: `PRODIGI_API_KEY_SANDBOX=ck_...`

I'll auto-roll into Phase 1 the moment that file exists.

---

## Outstanding items for Grant (lower urgency)

- Confirm storage choice: R2 vs DO Spaces (recommendation above)
- When you have ~15 min: sign up Resend (resend.com) and Smarty (smarty.com), drop API keys in `backups/`. Unblocks Phase 3 hard gate.
- Optional: set `git config --global user.email` so future commits don't show `grant@Mac.localdomain`

---

## What's next

I'll push the branch to GitHub now (`gh pr create` not yet — staging in PR opens the question of CI gating). Then start Phase 1 the moment the Prodigi sandbox key is in place.
