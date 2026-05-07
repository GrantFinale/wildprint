# Cron / scheduler

Phase 5a wired up `rq-scheduler` so background jobs run on a schedule
without us writing any one-off cron infra. The scheduler runs as a third
Coolify service alongside `web` + `worker`.

## Job catalog

| Job ID                      | Cadence            | What it does                                                                 |
| --------------------------- | ------------------ | ---------------------------------------------------------------------------- |
| `drain_outbox`              | every 30 seconds   | Pull pending outbox rows, render templates, POST to Resend.                  |
| `refresh_prodigi_quotes`    | daily 03:00 UTC    | Refresh the quote cache for every active SKU (Prodigi v4.0 sandbox).         |
| `cleanup_render_outputs`    | weekly             | Delete tier-1/2 `render_outputs` older than 90 days from Spaces + DB.        |
| `monitor_failed_callbacks`  | every 15 minutes   | Sentry alert when `prodigi_callbacks` has rows stuck in `error` for >1h.     |
| `monitor_dead_outbox`       | every 30 minutes   | Sentry alert on any `outbox` rows in `dead` state.                           |

Cadences are constants in `review_app/scheduler/cron.py`. Bump them and
redeploy — the scheduler cancels the old schedule and re-registers on
boot, so no migration is needed.

Tier-3 (high-res print) renders are NOT touched by `cleanup_render_outputs`
— they're retained for 18 months per the project retention policy. A
separate `cleanup_tier3_renders` job will be scheduled when retention
becomes an actual cost concern.

## Coolify setup

The scheduler is a separate Docker service that mirrors the worker's
config except for the entrypoint:

1. **Image:** `Dockerfile.scheduler` (in repo root). Same base + system
   deps as `Dockerfile.worker`.
2. **CMD:** `python -m scripts.run_scheduler`.
3. **Replicas:** **1 only** — `rq-scheduler` does not support multi-instance
   leader election. Two scheduler containers will register every cron
   entry twice, causing duplicate enqueues. Coolify "Single instance" mode.
4. **Env vars:** same as the worker (`REDIS_URL`, `DATABASE_URL`, plus
   every API key the cron jobs touch — `PRODIGI_*` for the quote refresh,
   `SPACES_*` for cleanup).
5. **Stop timeout:** `--stop-timeout=10` is plenty (the scheduler doesn't
   hold long-running work; jobs run on the worker).
6. **Healthcheck:** built into the Dockerfile (`pgrep -f run_scheduler`).
7. **Rolling deploys:** safe. There's a brief gap with no scheduler, but
   every job's interval is ≥30s so nothing is missed catastrophically.

### Coolify wiring (mirroring the worker config)

```text
Service name:    scheduler
Build:           Docker — Dockerfile.scheduler
Replicas:        1 (HARD requirement — see above)
Restart:         on-failure
Health probe:    pgrep -f scripts.run_scheduler
Env vars:        REDIS_URL=<same as web/worker>
                 DATABASE_URL=<same as web/worker>
                 SPACES_ACCESS_KEY_ID, SPACES_SECRET_ACCESS_KEY,
                 SPACES_REGION, SPACES_ENDPOINT, SPACES_THUMBS_BUCKET
                 PRODIGI_SANDBOX_API_KEY (or PRODIGI_LIVE_API_KEY)
                 RESEND_API_KEY (so monitor jobs can attempt notifications)
                 SENTRY_DSN
                 STRUCTLOG_JSON=true (matches worker logging)
```

## Triggering a job manually

For post-deploy dogfooding or admin "fire now" buttons:

```bash
flask cron-fire <job_id>
```

`<job_id>` is one of `drain_outbox`, `refresh_prodigi_quotes`,
`cleanup_render_outputs`, `monitor_failed_callbacks`, `monitor_dead_outbox`.
This enqueues a one-shot job on the default queue — the worker picks it
up immediately, no scheduler involvement.

Inside the admin UI, the same hook is exposed via a "Run now" button on
the Settings page (Phase 5b will land the UI; the CLI command works today).

## Tuning + overrides

| Env var                     | Default | Effect                                              |
| --------------------------- | ------- | --------------------------------------------------- |
| `SCHEDULER_INTERVAL_SECONDS`| 30      | How often the scheduler wakes to enqueue due jobs.  |

Per-job cadences require a code change in `review_app/scheduler/cron.py`
+ redeploy. The scheduler module is small; this is intentional — cadence
changes should land in code review like anything else.

## Test coverage

`tests/scheduler/test_scheduler.py` covers:

* singleton behavior of `get_scheduler()`
* `setup_cron_jobs` registers the full catalog
* idempotent re-registration (no duplicates after multiple calls)
* `enqueue_now` lands on the default queue
* each new monitor/cleanup job is callable end-to-end against an empty DB

8 tests, all run against fakeredis + an in-memory SQLite session.
