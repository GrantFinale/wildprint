# Phase 3 schema reference (Phase 3a deliverable)

This document is the working reference for the database schema added in
Phase 3a (migrations 0008–0013). For the original design narrative, see
[docs/db-schema.md](db-schema.md).

## Tables added

| Migration | Table | Purpose |
|---|---|---|
| `0008_customers` | `customers` | Buyer accounts + legacy lead fields. |
| `0009_addresses` | `addresses` | Shipping addresses + Smarty validation cache. |
| `0010_carts` | `carts`, `cart_items` | Multi-item shopping cart (anonymous-OK). |
| `0011_orders` | `orders`, `order_items` | Placed orders. Also adds `prodigi_orders.order_id` FK. |
| `0012_refunds` | `refunds` | Stripe refunds + Prodigi cancel attempts. |
| `0013_migrate_leads_json` | (data) | Idempotently copies `metadata/leads.json` → `customers` (+ synthetic `orders` for `paid: true` leads). |

## Foreign-key map (new edges)

```
customers.id ──┬── addresses.customer_id          (CASCADE delete)
               ├── carts.customer_id              (SET NULL — anonymous OK)
               └── orders.customer_id             (RESTRICT)

addresses.id ──── orders.shipping_address_id      (RESTRICT)

render_specs.id ──┬── cart_items.render_spec_id   (SET NULL)
                  └── order_items.render_spec_id  (RESTRICT — preserve history)

prodigi_skus.internal_sku ──┬── cart_items.prodigi_sku_internal  (RESTRICT)
                            └── order_items.prodigi_sku_internal (RESTRICT)

orders.id ──┬── order_items.order_id              (RESTRICT)
            ├── refunds.order_id                  (RESTRICT)
            └── prodigi_orders.order_id           (RESTRICT, nullable)

users.id ──── refunds.requested_by_user_id        (SET NULL — preserve audit)

carts.id ──── cart_items.cart_id                  (CASCADE)
```

### Why these `ON DELETE` choices?

* **CASCADE** for `addresses.customer_id` and `cart_items.cart_id` —
  child has no meaning without its parent, and we never expect to want
  the orphan.
* **RESTRICT** for `orders.customer_id`, `orders.shipping_address_id`,
  `order_items.order_id`, `refunds.order_id` — an order is a financial
  record. We never hard-delete; soft-delete only via `customers.deleted_at`.
  Even an admin "delete this customer" path must refuse if they have
  outstanding orders.
* **SET NULL** for `carts.customer_id`, `users.id → refunds.requested_by_user_id`,
  `render_specs.id → cart_items.render_spec_id` — preserve the row but
  release the broken pointer.
* **RESTRICT** for `render_specs.id → order_items.render_spec_id` —
  recommendation: keep render_specs forever for paid orders. The RESTRICT
  enforces this even if a pruner job is misconfigured.

## Index choices

| Index | Reason |
|---|---|
| `customers_email_active_uq` (partial, `WHERE deleted_at IS NULL`) | Email is the natural key but soft-deleted rows must not block re-signup. |
| `ix_customers_stripe_customer_id` (partial unique) | Look up by Stripe ID; many customers have no Stripe ID yet (NULL). |
| `addresses_one_default_per_customer_uq` (partial) | One default shipping per customer. Excludes deleted rows. |
| `ix_orders_customer_id_created_at` (descending) | "Show this customer's orders, newest first" — admin and self-service. |
| `ix_orders_status_created_at` (descending) | Admin queue: "all `pending` orders, oldest first" → reverse-iter. |
| `uq_orders_stripe_payment_intent_id` | Stripe idempotency anchor; webhook handler dedupes by this. |
| `uq_refunds_stripe_refund_id` | Same idempotency story for refunds. |
| `ix_carts_session_token`, `ix_carts_customer_id` | Anonymous and signed-in cart lookups. |
| `ix_order_items_order_id` | Order detail page joins. |
| `ix_refunds_order_id` | Refund history per order. |
| `ix_prodigi_orders_order_id` | New back-pointer added in 0011. |

## Status enums (TEXT + CHECK, never native ENUMs)

* `carts.status ∈ {open, abandoned, converted}`
* `orders.status ∈ {pending, paid, in_production, shipped, delivered, refunded, cancelled, problem}`
* `refunds.status ∈ {pending, succeeded, failed, cancelled}`

Rationale: native Postgres `ENUM` types are painful to ALTER under Alembic
(adding a value requires `ALTER TYPE ... ADD VALUE` outside a transaction).
TEXT + CHECK round-trips cleanly to SQLite for tests and lets us evolve
the value set with a one-line CHECK swap.

## Money

All monetary columns are **`BIGINT cents`** (no decimals, no floats). Currency
is stored on `orders.currency` (3-char ISO 4217 string, default `'USD'`).

## UUID type choice — go-forward convention

We use **`sa.Uuid(as_uuid=True)`** (the SQLAlchemy 2.0 native cross-dialect
type) for every new UUID column.

* On Postgres: native `UUID` (16-byte binary, indexed efficiently).
* On SQLite (tests): 32-char hex string column. SQLAlchemy round-trips
  `uuid.UUID` Python objects on both backends without per-call casting.

This matches the pattern introduced by `0007_render_specs.py` and supersedes
the dialect-branching `_uuid_col()` helper used by the older Phase 1
prodigi migrations. The new ORM models (`Customer`, `Address`, `Cart`,
`CartItem`, `Order`, `OrderItem`, `Refund`) all use `Uuid(as_uuid=True)`
in their `mapped_column(...)` calls. The Phase 1 prodigi models continue
to use `_uuid_col()` for backward compatibility — both produce equivalent
schemas.

## Coexistence with `metadata/leads.json`

Phase 3a does NOT delete `leads.json` and does NOT cut the legacy `$49 unlock`
flow over to the database. Both write paths coexist:

* The existing `_save_lead()` / `_mark_lead_paid()` code in `review_app/app.py`
  continues to write to `leads.json`.
* When the parallel agent's checkout work lands, it will additionally write
  to `customers` / `orders` for new physical-print purchases.
* Migration `0013` does a one-time **copy** (not a move) from `leads.json`
  into `customers` (+ synthetic `orders` for paid leads). The migration is
  **idempotent** — re-runs skip rows that already exist by email or by
  `stripe_payment_intent_id`. Migration is **reversible** — `downgrade()`
  deletes only rows tagged `created_by_migration='0013_migrate_leads_json'`
  (customers) and `source='legacy_$49_unlock'` (orders).

The `customers.legacy_lake_name` and `customers.legacy_state` columns
preserve the lake/state context that doesn't fit the orders model. They're
nullable for new (non-legacy) signups.

The `customers.created_by_migration` column is a free-form provenance tag
so future data migrations can scope their own `downgrade()` deletions
without touching app-created rows.

## Address validation flow (Smarty)

`review_app/addresses/__init__.py` exposes `validate_and_persist(session,
customer_id, AddressInput)`:

1. Calls `review_app.addresses.smarty.verify_address(...)` against
   `https://us-street.api.smarty.com/street-address` with `match=invalid`
   (so even bad addresses come back with their analysis).
2. Always inserts an `Address` row with `validation_provider='smarty'`,
   `dpv_match_code` set, and `validation_response` saved as JSONB.
3. Sets `validated_at = now()` only when `dpv_match_code IN {'Y', 'S', 'D'}`.
4. **The cart/checkout flow refuses to proceed when `Address.is_deliverable`
   is False.** We persist the rejected attempt anyway so we have an audit
   trail and can show the user what we tried.

Smarty credentials come from `SMARTY_AUTH_ID` + `SMARTY_AUTH_TOKEN` env
vars (read lazily inside `verify_address` so import never fails).

## Migration chain

```
0001_baseline → 0002_users → 0003_ai_usage → 0004_outbox →
0005_prodigi_orders → 0006_seed_prodigi_skus → 0007_render_specs →
0008_customers → 0009_addresses → 0010_carts → 0011_orders →
0012_refunds → 0013_migrate_leads_json
```

All Phase 3a migrations are reversible (`downgrade()` defined and tested).
The data migration's `downgrade()` is scoped — it deletes only the rows
it created (matched by `created_by_migration` / `source` sentinels).
