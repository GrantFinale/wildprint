# Admin shell — architecture & extension guide

**Phase 4a deliverable.** Documents the new `/admin/*` chrome that wraps every
admin page in a consistent shell (sidebar + topbar + role gating + breadcrumbs).

The Phase 4b parallel agent extends this shell with Orders / Fulfillment /
Customers / Content / Analytics pages; this document is the contract they
build against.

---

## 1. Module layout

```
review_app/admin/
├── __init__.py            # exports admin_bp + init_app(app)
├── routes.py              # the top-level admin_bp + dashboard route + stubs
├── nav.py                 # NAV_TREE — single source of truth for sidebar
├── context.py             # context processor (Prodigi env pill, build id)
├── dashboard.py           # stat-card + top-SKU query helpers
├── search.py              # /admin/search (Phase 5 placeholder)
├── _helpers.py            # current_role(), crumbs() utilities
├── _phase4b.py            # parallel agent's `register_phase4b_routes()`
├── _session.py            # request-bound DB session (parallel agent)
├── catalog/
│   ├── __init__.py        # side-effect import of routes
│   └── routes.py          # 6 Catalog pages
├── settings/
│   ├── __init__.py
│   └── routes.py          # 5 Settings pages
└── (orders, fulfillment, customers, content, analytics)/
    └── (parallel agent)
```

Templates live alongside under `review_app/templates/admin/` mirroring the
module tree (e.g., `templates/admin/catalog/species.html`). Static assets
under `review_app/static/{css,js}/admin/`.

---

## 2. Wiring

`review_app/app.py` (the wiring block already in production) calls
`_init_admin(app)` after `_init_auth(app)`:

```python
_init_auth(app)
app.register_blueprint(_auth_bp)
# ... other init_app(app) calls ...
_init_admin(app)   # MUST come after _init_auth so url_for('auth.login') works.
```

`_init_admin(app)` registers the single top-level blueprint `admin_bp`
(mounted at `/admin`). All admin pages — Dashboard, Catalog/*, Settings/*,
Search, plus the parallel agent's Orders/* etc. — are routes on this one
blueprint, so endpoint names are uniformly prefixed `admin.*`.

---

## 3. Role-gating model

Two layers, both backed by `review_app.auth.models.VALID_ROLES = {admin,
staff, viewer}`:

1. **Route gate** — `@requires_role("admin", ...)` from
   `review_app.auth.decorators`. Returns 401 (redirect to login) for anon,
   403 for wrong-role, otherwise runs the view. Fully no-ops when
   `ADMIN_AUTH_ENABLED` env var is unset (shadow mode), so a fresh dev
   environment shows everything.
2. **Sidebar visibility** — `nav.py` declares each `NavItem` with a
   `roles` tuple. The sidebar partial (`templates/admin/_sidebar.html`)
   filters the tree by `current_role()`. Items the role can't access are
   **hidden**, not greyed out (per IA decision); direct URL access still
   hits the route gate (returns 403).

The two layers MUST match: if you change a route's `@requires_role`, also
update the matching `NavItem.roles` in `nav.py`.

---

## 4. The 5-step recipe — adding a new admin page

1. **Add a `NavItem`** to the relevant `NavCategory` in `nav.py`. Decide the
   role tuple. Endpoint name follows `admin.<category>_<page>` convention.
2. **Write the route handler** in the appropriate sub-module
   (`admin/catalog/routes.py`, `admin/settings/routes.py`, etc.). Decorate
   with `@requires_role(...)` matching the NavItem roles. Call
   `render_template("admin/<category>/<page>.html", page_title=..., breadcrumbs=crumbs(...))`.
3. **Write the template** at `templates/admin/<category>/<page>.html`. Extend
   `admin/_base.html` and fill the `content` block. Optionally fill
   `scripts_extra` for per-page JS.
4. **Add a unit test** in `tests/admin/test_<category>.py` using the
   `client` + `role_setter` fixtures. Cover the role gate (forbidden roles
   get 403) and the 200 happy path.
5. **Run the verifier**: `pytest tests/admin/`, `mypy review_app/admin/`,
   `ruff check review_app/admin/`. All green = done.

---

## 5. Topbar context (always available in admin templates)

The `admin_bp` context processor (`context.py`) injects:

| Variable | Type | Source |
|---|---|---|
| `prodigi_env_pill` | `{label, css_class}` | `PRODIGI_ENV` env var (`sandbox`→red, `live`/`production`→green) |
| `admin_notifications` | `int` | `flask.g.admin_notifications` (Phase 5 polling endpoint) |
| `build_id` | `str` | `SENTRY_RELEASE` or `GIT_SHA` env, first 7 chars |
| `nav_categories` | `list[NavCategory]` | filtered by `current_role()` |
| `current_role` | `str | None` | from `_helpers.current_role()` |

---

## 6. Existing route migration map

| Legacy route | New home |
|---|---|
| `GET /admin` | `admin.dashboard` (`/admin`) — redirected to new shell |
| `GET /admin/data` | unchanged (kept in `app.py`); consumed by `templates/admin/catalog/{species,sizing}.html` |
| `POST /admin/species/<slug>/scale` | unchanged; consumed by Catalog/Species + Catalog/Sizing |
| `POST /admin/settings/global_size_variance` | unchanged; consumed by Catalog/Sizing |
| Background gallery (`/api/list-backgrounds`, etc.) | unchanged; consumed by `admin/catalog/backgrounds.html` |
| Coverage cards / search / category filter | embedded in `admin/catalog/species.html` |

The legacy single-page `/admin` (basic-auth gated) is **replaced** — the
blueprint owns `/admin` now and renders the new shell. The legacy JSON
endpoints (`/admin/data`, `/admin/species/.../scale`,
`/admin/settings/global_size_variance`) **remain in `app.py`** because
the migrated species/sizing templates call them directly. Phase 4b can
move them onto the blueprint once the shell is stable.

---

## 7. Auth coexistence (Phase 4b note)

The legacy HTTP Basic auth (`ADMIN_PASSWORD`) and the new Flask-Login
auth (`ADMIN_AUTH_ENABLED`) currently coexist:

* The legacy `/admin/data`, etc. are `@admin_required` (basic auth).
* The new shell pages are `@requires_role(...)` (Flask-Login).

Both use `ADMIN_PASSWORD` / `ADMIN_AUTH_ENABLED` flags that default to
shadow mode (no auth). Phase 4b's last commit migrates the legacy
endpoints fully onto Flask-Login + drops `admin_required`.

---

## 8. Phase 5 TODOs flagged in this work

1. **Audit log table** — referenced by `Settings / Audit log` but doesn't
   exist in the schema. Add Alembic migration + middleware to write rows
   on state-changing admin actions.
2. **Cmd+K real search** — `/admin/search` returns a placeholder page;
   wire the cross-entity search (orders, customers, species, SKUs).
3. **Notifications polling** — `admin.js` has a stub hook; wire to the
   real `/admin/api/notifications` endpoint that returns
   `{count, items: [{...}]}`.
4. **Integrations real probes** — `Settings / Integrations` reports
   "configured" based on env-var presence only. Phase 5 adds a background
   job that pings each upstream and writes to a `health_checks` table.
5. **Render presets editing** — currently read-only display of
   `TIER_CONFIG`; Phase 5 wires a per-tier override stored in a settings
   table.
6. **2FA + per-user API tokens** — stubbed on `Settings / My account`.
7. **Frame SKUs "Refresh all quotes" button** — disabled stub; wire to
   the existing `refresh_all_skus_job` worker function.
