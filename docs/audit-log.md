# Audit log

Phase 5a wired up the `audit_log` table and a Flask middleware that
auto-captures every state-changing admin action. Combined with explicit
`audit.record(...)` calls inside business code, this gives us a complete
compliance trail without sprinkling `INSERT INTO audit_log` everywhere.

## What's recorded

### Auto-capture (middleware)

Every successful POST/PATCH/PUT/DELETE under `/admin/*` writes one row:

```
action       = "http.POST"  (or http.PATCH / http.DELETE / http.PUT)
target_type  = "admin_route"
target_id    = "<request.path>"
after        = {"method", "path", "status", "endpoint"}
user_id      = current_user.get_id()  (NULL when shadow-mode auth)
ip_address   = X-Forwarded-For first, then request.remote_addr
user_agent   = request.headers["User-Agent"]  (truncated 1024 chars)
```

This catches everything by default. Routes opt out by adding the
`@audit.skip` decorator — currently used on the audit log viewer itself
so reading the log doesn't itself produce log entries.

Failed responses (4xx/5xx) are NOT recorded — they didn't change state.

### Explicit (business code)

For important actions, business code calls `audit.record(...)` with a
before/after snapshot so the diff is auditable:

```python
from review_app.audit import record

with get_session() as session:
    order = session.get(Order, order_id)
    before = {"status": order.status, "refunded_cents": order.refunded_cents}
    order.status = "refunded"
    order.refunded_cents = order.total_cents
    record(
        session,
        action="order.refund",
        target_type="order",
        target_id=str(order.id),
        before=before,
        after={"status": order.status, "refunded_cents": order.refunded_cents},
    )
```

The action naming convention is dotted: `<entity>.<verb>`. Examples:
`order.refund`, `sku.update`, `user.role_change`, `cart.merge_anonymous`.

## Schema

See `alembic/versions/0015_audit_log.py` for the migration and
`review_app/audit/models.py` for the SQLAlchemy ORM. Highlights:

* `id` BIGSERIAL PK.
* `user_id` UUID FK → users.id ON DELETE SET NULL — deactivating a user
  preserves their historical trail.
* `before` / `after` JSONB.
* `ip_address` INET on Postgres (TEXT on SQLite).
* `created_at` TIMESTAMPTZ DEFAULT now().

Indexes:

* `(user_id, created_at DESC)` — "what did this user do recently"
* `(action, created_at DESC)` — "every refund in the last 24h"
* `(target_type, target_id, created_at DESC)` — "history for THIS order"

## Append-only by convention

We do NOT enforce immutability at the DB level (no row-level security,
no UPDATE/DELETE triggers). The `audit.record(...)` helper is the only
writer inside the app. Manual deletions via `psql` are tolerated for dev
mode but discouraged in prod — the table is small even at scale (~2 KB
per row × 10 admin actions per day = ~7 MB after a year).

When/if we ship to a regulated context where this matters, the cleanest
upgrade path is: keep the table as-is, add a Postgres trigger that
raises on UPDATE/DELETE outside an explicit override role.

## Retention

No automatic deletion. Audit data is small and high-value; we keep it
forever unless/until it becomes a cost or compliance burden.

## Admin viewer

`GET /admin/settings/audit` (admin role only) renders the table with:

* Filters: user UUID, action, target_type, date range (ISO).
* Pagination: 50 entries per page.
* Newest first.
* Each row's `before` / `after` shown collapsed in a `<details>` block.

The viewer is `@audit.skip`'d so reading the log doesn't itself produce
audit events (otherwise the log would grow on every refresh).

## Failure modes

The middleware is wrapped in a `try/except Exception` — a DB error during
audit write is logged + the request continues. We tolerate occasional
missed audit rows in exchange for never breaking the user's flow. If
audit write failures become real, monitor via the existing structlog
pipeline (search for `audit.after_request failed`).
