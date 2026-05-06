# fishingposter.com — Database Schema

Postgres 16 (existing `benedict-ventures` container, host port 5433) + SQLAlchemy 2.0 (`Mapped[]` syntax, strict mypy) + Alembic.

## Conventions

- **Primary keys**: UUID v7 (`uuid-ossp` + a `uuidv7()` SQL function shipped in the first migration). Sortable like an integer, globally unique like a UUID, safe to expose in URLs. Exception: `prodigi_skus` uses a serial `id` plus a `UNIQUE` natural key on `internal_sku` since the catalog is small and admin-edited.
- **Status columns**: stored as `TEXT` with `CHECK (status IN (...))`. We avoid Postgres `ENUM` because Alembic migrations on enums are clumsy (`ALTER TYPE ... ADD VALUE` can't run inside a transaction).
- **Money**: stored as `BIGINT` cents in a single `currency CHAR(3)` column per row. Never floats.
- **Timestamps**: `TIMESTAMPTZ NOT NULL DEFAULT now()` for `created_at`. `updated_at` driven by a SQLAlchemy `Mapped[datetime] = mapped_column(onupdate=func.now())` event hook so the ORM owns it (no DB triggers to maintain).
- **Soft delete**: `deleted_at TIMESTAMPTZ NULL`. Partial indexes use `WHERE deleted_at IS NULL`.
- **JSONB everywhere** for vendor payloads (Prodigi, Smarty, Stripe). Indexed with GIN only when we actually query into them.

---

## 1. `users` — admin / staff

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    email           CITEXT NOT NULL,
    password_hash   TEXT NOT NULL,                 -- argon2id, includes params
    role            TEXT NOT NULL CHECK (role IN ('admin','staff','viewer')),
    last_login_at   TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX users_email_active_uq ON users (email) WHERE deleted_at IS NULL;
```

`CITEXT` keeps email matching case-insensitive without lower()-everywhere. Argon2id over bcrypt because we're greenfield and OWASP recommends it.

## 2. `customers` — end-user buyers

```sql
CREATE TABLE customers (
    id                   UUID PRIMARY KEY DEFAULT uuidv7(),
    email                CITEXT NOT NULL,
    name                 TEXT NULL,
    -- legacy fields preserved verbatim from leads.json for the cutover
    legacy_lake_name     TEXT NULL,
    legacy_state_code    CHAR(2) NULL,
    digital_unlock_paid  BOOLEAN NOT NULL DEFAULT FALSE,
    stripe_customer_id   TEXT NULL,
    marketing_opt_in     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at         TIMESTAMPTZ NULL,
    deleted_at           TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX customers_email_active_uq ON customers (email) WHERE deleted_at IS NULL;
CREATE INDEX customers_stripe_customer_id_ix ON customers (stripe_customer_id);
```

`legacy_*` columns hold the lake/state from `leads.json` as denormalized hints — they're not the source of truth post-migration but they answer "what was this person looking at when they signed up?" for marketing.

## 3. `addresses`

```sql
CREATE TABLE addresses (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    customer_id      UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    label            TEXT NULL,                            -- "Home", "Work"
    recipient_name   TEXT NOT NULL,
    line1            TEXT NOT NULL,
    line2            TEXT NULL,
    city             TEXT NOT NULL,
    region           TEXT NOT NULL,                        -- state for US
    postal_code      TEXT NOT NULL,
    country_code     CHAR(2) NOT NULL DEFAULT 'US',
    phone            TEXT NULL,
    smarty_status    TEXT NOT NULL DEFAULT 'unvalidated'
        CHECK (smarty_status IN ('unvalidated','valid','invalid','ambiguous','error')),
    smarty_response  JSONB NULL,
    validated_at     TIMESTAMPTZ NULL,
    is_default       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX addresses_customer_id_ix ON addresses (customer_id);
CREATE UNIQUE INDEX addresses_one_default_per_customer_uq
    ON addresses (customer_id) WHERE is_default;
```

CASCADE delete: addresses are meaningless without a customer. Smarty raw response in JSONB so we can audit a delivery dispute later without paying for re-lookup.

## 4. `render_specs` — content-addressable poster recipe

```sql
CREATE TABLE render_specs (
    id                UUID PRIMARY KEY DEFAULT uuidv7(),
    spec_hash         CHAR(64) NOT NULL,            -- sha256 of canonical JSON
    canonical_inputs  JSONB NOT NULL,               -- {lake, species, art_style, layout, seeds, ...}
    renderer_version  TEXT NOT NULL,                -- e.g. "editorial-v3.2"
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX render_specs_hash_uq ON render_specs (spec_hash);
CREATE INDEX render_specs_inputs_gin ON render_specs USING GIN (canonical_inputs jsonb_path_ops);
```

**Why content-addressable:** the renderer is deterministic given (inputs + version). Two customers ordering the same lake/species/style get the same `spec_hash` and we serve the same cached tier-1/tier-2 files. `renderer_version` is part of the hash input so a renderer change forces a regen without manual cache busting.

## 5. `render_outputs` — one row per (spec, tier)

```sql
CREATE TABLE render_outputs (
    id                UUID PRIMARY KEY DEFAULT uuidv7(),
    render_spec_id    UUID NOT NULL REFERENCES render_specs(id) ON DELETE CASCADE,
    tier              SMALLINT NOT NULL CHECK (tier IN (1,2,3)),
    storage_path      TEXT NOT NULL,                -- s3://bucket/key or /local/path
    content_hash      CHAR(64) NOT NULL,            -- sha256 of bytes
    file_size_bytes   BIGINT NOT NULL,
    width_px          INTEGER NOT NULL,
    height_px         INTEGER NOT NULL,
    mime_type         TEXT NOT NULL,
    queue_job_id      TEXT NULL,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX render_outputs_spec_tier_uq ON render_outputs (render_spec_id, tier);
CREATE INDEX render_outputs_generated_at_ix ON render_outputs (generated_at);
```

CASCADE on `render_spec_id`: if we ever purge a spec, its outputs are dead weight. Tier 3 rows only exist post-payment; tier 1/2 rows are created on demand. The unique `(spec, tier)` index is the cache lookup.

## 6. `carts`

```sql
CREATE TABLE carts (
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    customer_id   UUID NULL REFERENCES customers(id) ON DELETE SET NULL,
    session_token TEXT NULL,                        -- for anonymous carts
    status        TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','abandoned','converted')),
    converted_order_id UUID NULL,                   -- FK added after orders exists
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX carts_customer_id_ix ON carts (customer_id);
CREATE INDEX carts_session_token_ix ON carts (session_token) WHERE status = 'open';
```

`SET NULL` on customer delete so abandoned-cart analytics survive a GDPR delete request. Anonymous carts use `session_token`; logging in attaches `customer_id`.

## 7. `cart_items`

```sql
CREATE TABLE cart_items (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    cart_id         UUID NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    render_spec_id  UUID NOT NULL REFERENCES render_specs(id) ON DELETE RESTRICT,
    prodigi_sku_id  INTEGER NULL REFERENCES prodigi_skus(id) ON DELETE RESTRICT,
    quantity        INTEGER NOT NULL CHECK (quantity > 0 AND quantity <= 99),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cart_items_cart_id_ix ON cart_items (cart_id);
```

`prodigi_sku_id NULL` = digital-only $49 unlock line. RESTRICT on render_spec / SKU because an item that references a vanished spec is a bug; force the cleanup to happen explicitly.

## 8. `orders`

```sql
CREATE TABLE orders (
    id                       UUID PRIMARY KEY DEFAULT uuidv7(),
    customer_id              UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    shipping_address_id      UUID NULL REFERENCES addresses(id) ON DELETE RESTRICT,
    cart_id                  UUID NULL REFERENCES carts(id) ON DELETE SET NULL,
    status                   TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','paid','in_production','shipped',
                          'delivered','refunded','cancelled','problem')),
    subtotal_cents           BIGINT NOT NULL,
    tax_cents                BIGINT NOT NULL DEFAULT 0,
    shipping_cents           BIGINT NOT NULL DEFAULT 0,
    total_cents              BIGINT NOT NULL,
    currency                 CHAR(3) NOT NULL DEFAULT 'USD',
    stripe_payment_intent_id TEXT NULL,
    stripe_checkout_session_id TEXT NULL,
    placed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at                  TIMESTAMPTZ NULL,
    notes                    TEXT NULL
);
CREATE UNIQUE INDEX orders_stripe_pi_uq ON orders (stripe_payment_intent_id)
    WHERE stripe_payment_intent_id IS NOT NULL;
CREATE INDEX orders_customer_id_ix ON orders (customer_id);
CREATE INDEX orders_status_placed_at_ix ON orders (status, placed_at DESC);
```

RESTRICT on customer/address deletion: orders are financial records, never auto-purge. The `(status, placed_at)` index serves the admin dashboard "open orders newest first" query.

## 9. `order_items`

```sql
CREATE TABLE order_items (
    id                   UUID PRIMARY KEY DEFAULT uuidv7(),
    order_id             UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    render_spec_id       UUID NOT NULL REFERENCES render_specs(id) ON DELETE RESTRICT,
    prodigi_sku_id       INTEGER NULL REFERENCES prodigi_skus(id) ON DELETE RESTRICT,
    quantity             INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents     BIGINT NOT NULL,
    line_total_cents     BIGINT NOT NULL,
    -- denormalized snapshot for receipts/emails — never reformat from live SKU
    snapshot_size_label  TEXT NOT NULL,             -- "16x20"
    snapshot_finish_label TEXT NOT NULL,            -- "Antique Gold"
    snapshot_internal_sku TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX order_items_order_id_ix ON order_items (order_id);
```

Denormalized snapshots so a receipt mailed today still says "Antique Gold" even if we rename that finish in the catalog tomorrow.

## 10. `prodigi_skus`

```sql
CREATE TABLE prodigi_skus (
    id                          SERIAL PRIMARY KEY,
    internal_sku                TEXT NOT NULL,        -- "FP-CLA-16X20-AGOLD"
    prodigi_sku                 TEXT NOT NULL,        -- "GLOBAL-CFPM-16X20"
    prodigi_attributes          JSONB NOT NULL,       -- {"color":"antique-gold"}
    finish                      TEXT NOT NULL CHECK (finish IN
        ('Black','White','Natural','Antique Silver','Brown',
         'Antique Gold','Dark Grey','Light Grey')),
    size_inches                 TEXT NOT NULL,        -- "16x20"
    orientation                 TEXT NOT NULL CHECK (orientation IN
        ('portrait','landscape','square')),
    retail_price_cents          BIGINT NOT NULL,
    last_quoted_wholesale_cents BIGINT NULL,
    margin_cents                BIGINT GENERATED ALWAYS AS
        (retail_price_cents - COALESCE(last_quoted_wholesale_cents, 0)) STORED,
    in_stock                    BOOLEAN NOT NULL DEFAULT TRUE,
    last_refreshed_at           TIMESTAMPTZ NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX prodigi_skus_internal_uq ON prodigi_skus (internal_sku);
CREATE UNIQUE INDEX prodigi_skus_prodigi_attrs_uq
    ON prodigi_skus (prodigi_sku, (prodigi_attributes->>'color'));
```

`SERIAL` here because the catalog is ~32 rows, hand-curated, and the integer ID makes admin URLs short. `margin_cents` is a generated column so it's always in sync.

## 11. `prodigi_orders`

```sql
CREATE TABLE prodigi_orders (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    order_id            UUID NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    prodigi_order_id    TEXT NULL,                   -- "ord_..." once created
    idempotency_key     TEXT NOT NULL,               -- ours; sent on POST
    status_stage        TEXT NULL,                   -- per Prodigi vocabulary
    last_fetched_at     TIMESTAMPTZ NULL,
    raw_snapshot        JSONB NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX prodigi_orders_idempotency_uq ON prodigi_orders (idempotency_key);
CREATE UNIQUE INDEX prodigi_orders_prodigi_id_uq ON prodigi_orders (prodigi_order_id)
    WHERE prodigi_order_id IS NOT NULL;
```

One-to-one with `orders` (`UNIQUE` on `order_id`). `raw_snapshot` is the latest full GET — JSONB so we can add new query patterns without migrations.

## 12. `prodigi_callbacks`

```sql
CREATE TABLE prodigi_callbacks (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    event_id            TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    prodigi_order_id    TEXT NULL,
    raw_payload         JSONB NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ NULL,
    processed_status    TEXT NOT NULL DEFAULT 'pending'
        CHECK (processed_status IN ('pending','ok','error','retry','ignored')),
    error_message       TEXT NULL
);
CREATE UNIQUE INDEX prodigi_callbacks_event_id_uq ON prodigi_callbacks (event_id);
CREATE INDEX prodigi_callbacks_prodigi_order_ix
    ON prodigi_callbacks (prodigi_order_id) WHERE prodigi_order_id IS NOT NULL;
CREATE INDEX prodigi_callbacks_unprocessed_ix
    ON prodigi_callbacks (received_at) WHERE processed_status IN ('pending','retry');
```

`event_id` UNIQUE = idempotent webhook. Partial index on unprocessed events keeps the worker poll cheap.

## 13. `shipments`

```sql
CREATE TABLE shipments (
    id                    UUID PRIMARY KEY DEFAULT uuidv7(),
    order_id              UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    prodigi_shipment_id   TEXT NOT NULL,
    carrier_name          TEXT NULL,
    carrier_service       TEXT NULL,
    tracking_number       TEXT NULL,
    tracking_url          TEXT NULL,
    shipped_at            TIMESTAMPTZ NULL,
    delivered_at          TIMESTAMPTZ NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX shipments_prodigi_shipment_uq ON shipments (prodigi_shipment_id);
CREATE INDEX shipments_order_id_ix ON shipments (order_id);
```

A Prodigi order can split into multiple shipments — denormalized from callbacks for fast customer-facing lookup.

## 14. `refunds`

```sql
CREATE TABLE refunds (
    id                              UUID PRIMARY KEY DEFAULT uuidv7(),
    order_id                        UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    stripe_refund_id                TEXT NOT NULL,
    amount_cents                    BIGINT NOT NULL CHECK (amount_cents > 0),
    reason                          TEXT NULL,
    requested_by_user_id            UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    prodigi_cancellation_attempted  BOOLEAN NOT NULL DEFAULT FALSE,
    prodigi_cancellation_succeeded  BOOLEAN NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX refunds_stripe_refund_uq ON refunds (stripe_refund_id);
CREATE INDEX refunds_order_id_ix ON refunds (order_id);
```

Prodigi's cancel window is tiny (~30 min); we record both whether we tried and whether it worked so finance can reconcile.

## 15. `ai_usage_log`

```sql
CREATE TABLE ai_usage_log (
    id              BIGSERIAL PRIMARY KEY,            -- high-volume, sortable
    provider        TEXT NOT NULL,                    -- openai|recraft|replicate
    model           TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    render_spec_id  UUID NULL REFERENCES render_specs(id) ON DELETE SET NULL,
    tokens_in       INTEGER NULL,
    tokens_out      INTEGER NULL,
    units           NUMERIC(10,4) NULL,               -- e.g. image count
    cost_cents      INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    request_id      TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_usage_provider_created_ix ON ai_usage_log (provider, created_at DESC);
CREATE INDEX ai_usage_render_spec_ix ON ai_usage_log (render_spec_id) WHERE render_spec_id IS NOT NULL;
```

`BIGSERIAL` because this is append-only metrics where insert speed matters more than UUID uniqueness across nodes.

## 16. `audit_log`

```sql
CREATE TABLE audit_log (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    action       TEXT NOT NULL,                        -- "order.refund", "sku.update"
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    before       JSONB NULL,
    after        JSONB NULL,
    ip_address   INET NULL,
    user_agent   TEXT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_user_created_ix ON audit_log (user_id, created_at DESC);
CREATE INDEX audit_log_target_ix ON audit_log (target_type, target_id);
```

Append-only. `INET` is the native Postgres type for IPs (handles v4 + v6, queryable by subnet).

## 17. `outbox`

```sql
CREATE TABLE outbox (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    type            TEXT NOT NULL,                    -- email.order_confirmation, prodigi.create_order
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sending','sent','failed','dead')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error      TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX outbox_due_ix ON outbox (next_retry_at)
    WHERE status IN ('pending','failed');
```

**Why outbox:** the Stripe webhook handler must do "mark order paid" + "create Prodigi order" + "send confirmation email" atomically from the customer's perspective. With a true atomic DB transaction wrapping the row write plus an outbox enqueue, the worker can fan out without risking the "we charged the card but never made the print" failure mode.

---

## SQLAlchemy snippet (model style)

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.uuidv7())
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    status: Mapped[OrderStatus] = mapped_column(String, default=OrderStatus.PENDING)
    total_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    placed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    paid_at: Mapped[datetime | None]

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(cascade="all, delete-orphan")
```

---

## Migration ordering

Alembic revision graph (parents → children):

1. `0001_extensions` — `CREATE EXTENSION citext`, install `uuidv7()` SQL function.
2. `0002_users`
3. `0003_customers`
4. `0004_addresses` (FK → customers)
5. `0005_render_specs`
6. `0006_render_outputs` (FK → render_specs)
7. `0007_prodigi_skus`
8. `0008_carts` (FK → customers)
9. `0009_cart_items` (FK → carts, render_specs, prodigi_skus)
10. `0010_orders` (FK → customers, addresses, carts)
11. `0011_orders_carts_circular` — add `carts.converted_order_id` FK back to orders.
12. `0012_order_items` (FK → orders, render_specs, prodigi_skus)
13. `0013_prodigi_orders` (FK → orders)
14. `0014_prodigi_callbacks`
15. `0015_shipments` (FK → orders)
16. `0016_refunds` (FK → orders, users)
17. `0017_ai_usage_log` (FK → render_specs)
18. `0018_audit_log` (FK → users)
19. `0019_outbox`
20. `0020_seed_prodigi_skus` — data migration, the 32 launch SKUs.
21. `0021_migrate_leads_json` — data migration from `metadata/leads.json`.

## Data migration: `leads.json` → `customers`

Single Alembic data revision. Reads the JSON file, inserts one `customers` row per record, preserves `created_at`, copies `lake_name`/`state` into `legacy_*` columns, sets `digital_unlock_paid` from `paid`, and stores `stripe_session_id` on a synthetic `orders` row only when `paid=true` (status = `paid`, no shipping address, no order_items — represents the legacy $49 unlock). Idempotent: `ON CONFLICT (email) DO NOTHING`. Original file copied to `metadata/leads.json.pre-migration.bak` by the migration before reading.

## Seed data: 32 launch SKUs

Pattern: `prodigi_sku = 'GLOBAL-CFPM-{SIZE}'`, attribute `{"color": "<finish-slug>"}`. Sizes: `8X10`, `12X16`, `16X20`, `20X28`. Finishes: 8 verbatim from decision #6.

```sql
INSERT INTO prodigi_skus
    (internal_sku, prodigi_sku, prodigi_attributes, finish, size_inches,
     orientation, retail_price_cents, in_stock)
VALUES
  ('FP-CLA-08X10-BLK',   'GLOBAL-CFPM-8X10',  '{"color":"black"}',         'Black',         '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-WHT',   'GLOBAL-CFPM-8X10',  '{"color":"white"}',         'White',         '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-NAT',   'GLOBAL-CFPM-8X10',  '{"color":"natural"}',       'Natural',       '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-ASIL',  'GLOBAL-CFPM-8X10',  '{"color":"antique-silver"}','Antique Silver','8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-BRN',   'GLOBAL-CFPM-8X10',  '{"color":"brown"}',         'Brown',         '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-AGLD',  'GLOBAL-CFPM-8X10',  '{"color":"antique-gold"}',  'Antique Gold',  '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-DGRY',  'GLOBAL-CFPM-8X10',  '{"color":"dark-grey"}',     'Dark Grey',     '8x10',  'portrait', 4900,  TRUE),
  ('FP-CLA-08X10-LGRY',  'GLOBAL-CFPM-8X10',  '{"color":"light-grey"}',    'Light Grey',    '8x10',  'portrait', 4900,  TRUE),
  -- 12x16 block ($79 retail), 16x20 block ($109), 20x28 block ($149)
  -- repeat the same 8 finish rows for each size; pricing scales with size.
  ('FP-CLA-12X16-BLK',   'GLOBAL-CFPM-12X16', '{"color":"black"}',         'Black',         '12x16', 'portrait', 7900,  TRUE),
  -- ... (28 more rows: 7 finishes × 12X16, 8 × 16X20, 8 × 20X28) ...
  ('FP-CLA-20X28-LGRY',  'GLOBAL-CFPM-20X28', '{"color":"light-grey"}',    'Light Grey',    '20x28', 'portrait', 14900, TRUE);
```

Wholesale prices left NULL until the first live Prodigi quote populates `last_quoted_wholesale_cents`; the generated `margin_cents` column then surfaces in the admin dashboard.

## Non-obvious decision rationale (recap)

- **Outbox pattern over direct calls**: Stripe webhook handler can't both mark paid AND call Prodigi atomically — outbox lets a single DB transaction guarantee both happen-after-payment.
- **Content-addressable render_specs**: deterministic renderer means same inputs = same hash = cache hit; no manual eviction.
- **JSONB raw_snapshot vs columns**: Prodigi adds fields without warning; storing the full payload future-proofs us. Hot fields (`status_stage`, `prodigi_order_id`) are still promoted to columns for indexing.
- **TEXT + CHECK over Postgres ENUM**: Alembic-friendly status changes.
- **UUID v7 over v4**: btree-friendly insert order — avoids index hot-spots that random UUIDs cause.
- **Soft-delete only on users/customers**: financial records (orders, refunds, audit_log) are never deleted.
