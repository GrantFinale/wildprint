# Admin pages — Phase 4b reference

This document covers every admin page added in Phase 4b: Orders, Fulfillment,
Customers, Content, Analytics. The Phase 4a parallel agent owns the shell
template (sidebar, topbar, role-aware nav), the Dashboard, the Catalog
sub-tree, and the Settings sub-tree. See `docs/admin-ia.md` for the full
information architecture; this file is the per-page contract for the
modules in `review_app/admin/{orders,fulfillment,customers,content,analytics}`.

## How to add a new admin page

1. **Create the module.** New top-level area: add `review_app/admin/<area>/`
   with `__init__.py` (re-exports `register`) and `routes.py` (defines
   `register(admin_bp)` and the view functions).
2. **Register routes.** Inside `register(admin_bp)`, decorate views with
   `@admin_bp.route("/<path>", endpoint="<area>_<action>")`. Endpoints
   must follow the `admin.<area>_<action>` shape so the sidebar nav in
   `review_app/admin/nav.py` resolves them via `url_for(...)`.
3. **Gate the route.** Decorate every view with `@requires_role(...)`
   from `review_app.auth.decorators`. Pick the role tuple from the IA
   matrix (`docs/admin-ia.md` §3, "Roles & gating"). Viewers get read on
   list/detail pages; admin/staff get write on operational pages; admin
   only on settings + refunds.
4. **Get a DB session.** Use the helpers from
   `review_app.admin._session`:
   ```python
   from review_app.admin import _session as _admin_session

   session = _admin_session.get_session()
   try:
       ...
       return make_response(html)
   finally:
       _admin_session.close_session_if_owned(session, commit=False)
   ```
   The module attribute pattern (`_admin_session.get_session()` rather
   than `from ._session import get_session`) is required so tests can
   monkey-patch the function via `monkeypatch.setattr(_session,
   "get_session", ...)` and have the route see the patched version.
5. **Render a template.** Create `review_app/templates/admin/<area>/<page>.html`
   that extends `admin/_base.html` (owned by Phase 4a). Available blocks:
   `title`, `extra_head`, `head_extra`, `page_actions`, `content`,
   `scripts_extra`. The base injects globals via the admin context
   processor: `nav_categories`, `current_role`, `prodigi_env_pill`,
   `admin_notifications`, `build_id`.
6. **Wire into the Phase 4b registrar.** Add an import + call inside
   `review_app/admin/_phase4b.register_phase4b_routes`. The Phase 4a
   `routes.py` calls this once with the shared `admin_bp`.
7. **Test it.** Add a `tests/admin/test_<area>_admin.py` next to the
   existing module tests. The shared `admin_app` and `admin_client`
   fixtures (`tests/admin/conftest.py`) auto-register every Phase 4b
   sub-module. Use `db_session` for ORM writes; the patched `get_session`
   makes them visible to the route handler.

## Coordination with the Phase 4a shell

* **Owned by Phase 4a (do not touch from 4b):**
  `review_app/admin/__init__.py`, `routes.py`, `context.py`, `nav.py`,
  `_helpers.py`, `dashboard.py`, the entire `catalog/` and `settings/`
  sub-trees, plus `templates/admin/_base.html`, `_sidebar.html`,
  `_topbar.html`, `dashboard.html`, `search.html`, `_stub.html`.
* **Owned by Phase 4b:** `review_app/admin/_phase4b.py`,
  `_session.py`, `orders/`, `fulfillment/`, `customers/`, `content/`,
  `analytics/`, plus the matching template sub-trees.
* **Wiring:** Phase 4a's `routes.py` (when it lands) is expected to call
  `from review_app.admin._phase4b import register_phase4b_routes;
  register_phase4b_routes(admin_bp)` after defining its own routes.

## 1. Orders module

| Route | Endpoint | Roles (R/W) | Source |
| --- | --- | --- | --- |
| `GET /admin/orders` | `admin.orders_list` | admin/staff R+W, viewer R | `orders` JOIN `customers` JOIN `prodigi_orders` |
| `GET /admin/orders/<id>` | `admin.orders_detail` | admin/staff R+W, viewer R | `orders`, `order_items`, `customers`, `addresses`, `prodigi_orders`, `shipments`, `prodigi_callbacks`, `outbox` |
| `GET /admin/orders/refunds` | `admin.orders_refunds` | admin only | `refunds` |
| `GET/POST /admin/orders/test` | `admin.orders_test` | admin/staff | writes `customers`, `addresses`, `orders`, `order_items`, `outbox` |

**List page** has tabs (All / Open / In production / Shipped / Refunded /
Problem) with row-count badges, filters (search, SKU, date range,
problem-only), pagination (50/page), and a CSV export branch
(`?format=csv`).

**Detail page** sections: header w/ status badge + Refund/Reprint action
buttons, Customer panel, Line items, Payment summary, Prodigi timeline
(with raw event log collapsible), Shipping address, Notes (Phase 5),
Emails sent (with Resend action that POSTs to the email-log route).

**Refunds queue** is read-only listing; processing actions are stubbed
to anchors for Phase 5.

**Test order creator** creates a real Order with `source='admin_test'`,
`stripe_payment_intent_id='test_pi_<hex>'`, `status='paid'`. Enqueues an
outbox row of kind `prodigi.create_order` with payload
`{"order_id": "...", "test_order": True, "force_sandbox": True}` so the
existing worker routes the order through the Prodigi sandbox client.
The `source != 'admin_test'` filter in the analytics queries hides
these rows from revenue dashboards.

## 2. Fulfillment module

| Route | Endpoint | Roles (R/W) | Source |
| --- | --- | --- | --- |
| `GET/POST /admin/fulfillment/connection` | `admin.fulfillment_connection` | admin only | env vars + `prodigi_callbacks` for last-success ts |
| `GET /admin/fulfillment/webhooks` | `admin.fulfillment_webhooks` | admin/staff/viewer | `prodigi_callbacks` |
| `GET /admin/fulfillment/errors` | `admin.fulfillment_errors` | admin/staff | `orders.status='problem'` UNION `prodigi_callbacks.processed_status='error'` |
| `GET/POST /admin/fulfillment/reprints` | `admin.fulfillment_reprints` | admin only | writes `orders` (source='reprint') |

**Connection** displays masked API key, env (sandbox/prod), callback
URL, and last successful Prodigi callback timestamp. POST `action=ping`
calls `_ping_prodigi()` which hits `GET /Products/<known sku>` against
the active client and reports OK/FAIL inline.

**Webhook log** streams the most recent 200 callbacks with filters by
event type and processed status. Replay/mark-resolved actions are
Phase 5.

**Error queue** combines orders flagged as `problem` with callback rows
that failed handling. The error class column is computed from the
message text via `_classify_error()` (rejection / address /
image-quality / other).

**Reprint creator** spawns a fresh order with `source='reprint'` that
shares the original's customer + address + line items but with
`unit_price_cents=0` and the internal cost recorded into `tax_cents`
(Phase 5 will move it to a dedicated column).

## 3. Customers module

| Route | Endpoint | Roles (R/W) | Source |
| --- | --- | --- | --- |
| `GET /admin/customers` | `admin.customers_list` | admin/staff R+W, viewer R | `customers` + aggregated `orders` (`_ltv_by_customer`) |
| `GET /admin/customers/<id>` | `admin.customers_detail` | admin/staff R+W, viewer R | `customers`, `addresses`, `orders`, `outbox` |

**List** filters by search (email/name) and LTV bucket (>$0, >$200,
>$500). Adds a problem-flag badge per row when the customer has any
unresolved problem order.

**Detail** aggregates profile, addresses (with default + DPV badge),
linked orders, and email rows from the outbox. Notes/ban actions are
Phase 5.

LTV is the sum of `total_cents` across orders excluding `refunded` and
`cancelled` statuses. See `_ltv_by_customer()` in
`review_app/admin/customers/routes.py`.

## 4. Content module

| Route | Endpoint | Roles (R/W) | Source |
| --- | --- | --- | --- |
| `GET/POST /admin/content/email-templates` | `admin.content_email_templates` | admin only | in-memory `_email_templates` + outbox aggregate |
| `GET/POST /admin/content/email-log` | `admin.content_email_log` | admin/staff/viewer | `outbox` (kind starts with `email.`) |
| `GET/POST /admin/content/marketing` | `admin.content_marketing` | admin only | in-memory `_marketing_content` |

The 6 template kinds (`order_confirmed`, `in_production`, `shipped`,
`delivered`, `refunded`, `problem`) are stored as a Python dict keyed
by kind, replaced wholesale on save POST. Phase 5 will migrate this to
a `content_blocks` DB table.

**Test send** posts `action=test_send` with a recipient email and the
chosen kind; this enqueues an outbox row of kind `email.<kind>` with the
template's current subject + body, marked with `test_send: True`.

**Email log** filters by template kind and status. Resend POST takes a
`resend_id` (outbox row id) and re-enqueues the same kind + recipient
with `resent_from_id` annotated in the payload.

## 5. Analytics module

| Route | Endpoint | Roles (R) | Source |
| --- | --- | --- | --- |
| `GET /admin/analytics/sales` | `admin.analytics_sales` | all roles | `orders` + `order_items` |
| `GET /admin/analytics/ai-usage` | `admin.analytics_ai_usage` | all roles | `ai_usage_log` |
| `GET /admin/analytics/operations` | `admin.analytics_operations` | all roles | `orders` (placed_at/shipped_at deltas) |

All three accept `from`/`to` date params (default = last 30 days). Sales
also exports CSV via `?format=csv`. Charts are server-rendered HTML bar
elements (no JS-charting dep). The operations page approximates
"time-to-production" until Phase 5 adds an explicit
`orders.in_production_at` column.

The sales aggregates exclude orders with `source='admin_test'` so test
orders never inflate revenue.

## Testing

* `tests/admin/conftest.py` defines `admin_app`, `admin_client`, and
  `seed_minimal_catalog` fixtures. The conftest builds a minimal Flask
  app with a fresh `admin_bp`, registers all Phase 4b sub-modules, and
  patches `_session.get_session` to return the test's `db_session`.
* Tests use `tests/conftest.py`'s `db_session` (per-test SAVEPOINT-rolled-back
  SQLAlchemy session over an in-memory SQLite engine).
* Run: `pytest tests/admin/test_*_admin.py -v`.
* The auth and admin-search blueprints are stubbed in the test conftest
  so `url_for('auth.login')`, `url_for('auth.logout')`, and
  `url_for('admin.search')` resolve.

## SQLite UUID portability note

The `prodigi_orders.fishingposter_order_id` and `shipments.fishingposter_order_id`
columns use a dialect-portable type (`UUID` on Postgres, `TEXT` on
SQLite). When querying these columns from Python with a `uuid.UUID`
value, **convert to hex first on SQLite** to avoid the
`type 'UUID' is not supported` bind error. See `_bulk_prodigi_ids()`
in `review_app/admin/orders/routes.py` for the canonical pattern.
