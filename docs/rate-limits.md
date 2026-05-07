# Rate limits

Phase 5a wired up `flask-limiter` against the existing Redis instance so
rate counters are shared across web replicas. The legacy in-memory
`@rate_limit(20)` decorator on `/api/generate-poster` stays as a
per-process belt-and-braces guard.

## Limit table

| Endpoint                              | Per-minute default | Env var                       |
| ------------------------------------- | ------------------ | ----------------------------- |
| `POST /api/generate-poster`           | 5                  | `RATE_LIMIT_RENDER_PER_MIN`   |
| `POST /api/cart/add`                  | 30                 | `RATE_LIMIT_CART_PER_MIN`     |
| `POST /api/cart/items/.../update`     | 30                 | `RATE_LIMIT_CART_PER_MIN`     |
| `POST /api/cart/items/.../remove`     | 30                 | `RATE_LIMIT_CART_PER_MIN`     |
| `POST /api/checkout/start`            | 30                 | `RATE_LIMIT_CART_PER_MIN`     |
| **All other routes (default)**        | 120                | `RATE_LIMIT_GLOBAL_PER_MIN`   |

Limits are per identity. Anonymous traffic is keyed by IP
(`X-Forwarded-For` first, then `request.remote_addr`); authenticated
traffic uses `user_id+ip` so a single shared IP (office, school) doesn't
share one bucket.

A 429 response includes `Retry-After` and `X-RateLimit-*` headers
(flask-limiter sets these automatically when `headers_enabled=True`).

## Storage backend

Reads `REDIS_URL`. Falls back to in-process memory storage if that env
var is unset (local dev, unit tests).

A Redis backend is REQUIRED in prod — without it, each web replica
maintains its own bucket and a script can hammer N times the configured
limit by spreading requests across replicas.

## Exemptions

Two classes of routes bypass the limiter via `@limiter.request_filter`:

1. **`/admin/*`** — gated by Flask-Login + `@requires_role` + (in prod)
   a Coolify-level IP allowlist. Layering rate limits on top would just
   punish legitimate bursts of catalog edits.
2. **Webhook receivers** (`/api/stripe/webhook`, `/api/prodigi/webhook`)
   — authenticated by signature + idempotency. Rate-limiting them risks
   dropping a legitimate burst from Stripe/Prodigi.

If you add a new admin or webhook route, the exemption is automatic
(the filter looks at `request.path`).

## Tuning

Limits are read from env vars at app boot. To bump the render limit:

```bash
# Coolify env: RATE_LIMIT_RENDER_PER_MIN=10
# then redeploy / restart the web service
```

Invalid values (non-int) log a warning and fall back to the default.

## Test coverage

`tests/limits/test_limits.py` covers:

* render endpoint hits the limit at N+1 calls
* cart endpoint hits the limit at N+1 calls
* admin routes are exempt
* webhook routes are exempt
* env vars override defaults
* invalid env vars fall back gracefully

7 tests, all run against in-memory storage (no Redis required).

## Observability

When a request is rate-limited:

* 429 response with `Retry-After` header
* `X-RateLimit-Remaining: 0` header
* Logged at WARNING via flask-limiter's default logger

Add a Sentry breadcrumb if abuse becomes real (the limiter's
`on_breach` callback is the hook).
