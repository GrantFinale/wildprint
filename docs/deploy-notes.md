# Deploy notes — wildprint on Coolify

Operator-facing notes for deploying wildprint to Coolify on the
`benedict-ventures` droplet. Updated as new infra lands.

## Phase 0.4 — Redis + RQ render queue

### Redis container

The render queue needs a Redis instance reachable from both the web app
and the worker service. Two options on the droplet:

1. **Reuse Coolify's bundled Redis service** (preferred): provision a
   one-click Redis service in the Coolify UI. Use the internal
   `redis://service:6379/0` URL it generates.
2. **Stand up a dedicated Redis container** if isolation is required.

Either way, expose the URL to both services as `REDIS_URL`.

### Worker service config

Deploy the worker as a **separate Coolify service** from the web app —
not a sidecar — so it scales and rolls independently.

- **Source:** same Git repo as the web app.
- **Build:** Dockerfile path `wildprint/Dockerfile.worker`.
- **Env vars (required):**
  - `REDIS_URL` — same value as the web app uses.
  - Plus all env vars the web app needs that any job might touch
    (Stripe keys, R2 credentials, Resend, etc.) once those land in
    later sub-tasks. For Phase 0.4 only `REDIS_URL` is strictly needed.
- **Replicas:** start with 1; tier-3 renders are CPU-heavy (~5 min)
  but volume is low (one per print order).
- **Stop timeout:** **`--stop-timeout=300`** (5 minutes). The worker
  handles `SIGTERM` via RQ's warm-shutdown machinery — it finishes the
  current job, then exits. Coolify must give it the full 5 min grace
  window before sending `SIGKILL`, otherwise rolling deploys can
  truncate an in-flight print render.
- **Healthcheck:** baked into the Dockerfile (`pgrep` on the worker
  process). Coolify's container-level healthcheck is sufficient; no
  HTTP endpoint exposed.

### Env var matrix (Phase 0.4)

| Var         | Web | Worker | Notes                                  |
| ----------- | --- | ------ | -------------------------------------- |
| `REDIS_URL` | yes | yes    | Same value both sides                  |

### Smoke verification after deploy

```sh
# From the web container shell:
python -c "
from review_app.queue import enqueue
from review_app.queue.jobs import ping_job
job = enqueue(ping_job, 'deploy-smoke')
print('enqueued', job.id)
"

# Then from the worker container's logs, you should see the job
# pulled and finished within ~1 s.
```
