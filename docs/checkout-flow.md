# Checkout Flow (Phase 3b)

This document describes the end-to-end physical-product checkout pipeline
for fishingposter.com: cart → Stripe-hosted checkout → webhook → order
persisted → outbox drains → Prodigi order created + tier-3 render queued
+ confirmation email sent.

The legacy `$49 unlock` flow (digital download) is **untouched** by Phase 3b;
see [§ Legacy coexistence](#legacy-coexistence) below.

---

## End-to-end sequence

```
+----------+        +------------+        +--------+        +-------------+
| Browser  |        |  Flask     |        | Stripe |        | RQ worker   |
+----------+        +------------+        +--------+        +-------------+
     |                    |                   |                    |
     | 1. POST /api/cart/add (sku, render_spec_id, qty)
     |------------------->|                   |                    |
     |    200 {cart}      |                   |                    |
     |<-------------------|                   |                    |
     |                    |                   |                    |
     | 2. POST /api/checkout/start (cart_id, address_id, email)
     |------------------->|                   |                    |
     |                    | 2a. validate address (dpv_match_code in {Y,S,D})
     |                    | 2b. checkout.sessions.create
     |                    |------------------>|                    |
     |                    |   {url, id}       |                    |
     |                    |<------------------|                    |
     |    200 {url}       |                   |                    |
     |<-------------------|                   |                    |
     |                                                              |
     | 3. browser → Stripe-hosted checkout page
     |             ----------------------------->|                  |
     |                                           |                  |
     |  4. customer pays                         |                  |
     |                                           |                  |
     | 5. Stripe → POST /webhook/stripe/v2 (checkout.session.completed)
     |                    |<------------------|                    |
     |                    | 5a. verify Stripe-Signature
     |                    | 5b. INSERT stripe_events (UNIQUE on event_id)
     |                    | 5c. place_order_from_cart(cart, address, pi_id)
     |                    | 5d. enqueue OUTBOX rows:
     |                    |       - prodigi.create_order
     |                    |       - render.tier_3 (one per item)
     |                    |       - email.order_confirmed
     |                    | 5e. COMMIT
     |    200             |                                         |
     |<-------------------|                                         |
     |                                                              |
     |          6. RQ worker drains the outbox                      |
     |                                                              |
     |       6a. render.tier_3 → render_tier_job(spec_dict, 3, order_id)
     |                                                              |
     |       6b. prodigi.create_order → create_prodigi_order_job(order_id)
     |             — pre-flight: tier-3 RenderOutputRow must exist
     |             — POST /v4.0/Orders with Idempotency-Key=wp-{order_id}
     |             — persist prodigi_orders row, flip order.status='in_production'
     |             — enqueue email.in_production
     |                                                              |
     |       6c. email.order_confirmed → resend_client.send_via_resend
     |                                                              |
```

---

## Inputs

### `POST /api/cart/add`
```json
{
  "prodigi_sku_internal": "FP-CLA-16X20-AGOLD",
  "render_spec_id": "<UUID>",
  "quantity": 1
}
```
Resolves an open cart by `customer_id` (Flask-Login current_user) or by
the `wp_cart_session` cookie (HttpOnly + SameSite=Lax + 30-day max-age).

### `POST /api/checkout/start`
```json
{
  "cart_id": "<UUID>",
  "shipping_address_id": "<UUID>",
  "customer_email": "buyer@example.com",
  "marketing_opt_in": true
}
```
Returns `{ "url": "https://checkout.stripe.com/...", "checkout_session_id": "cs_..." }`.

Validation:
* shipping address: `dpv_match_code in {'Y', 'S', 'D'}` (Smarty USPS DPV).
* cart: must be `open` status, `item_count > 0`.
* customer_email: must contain `@`.

### `POST /webhook/stripe/v2`
Stripe-signed webhook. Verified via `STRIPE_WEBHOOK_SECRET_V2`
(falls back to `STRIPE_WEBHOOK_SECRET` in dev environments without the v2
var set).

---

## Idempotency strategy

| Layer | Anchor | Mechanism |
|---|---|---|
| Stripe webhook delivery | `event['id']` | `stripe_events.event_id UNIQUE` — duplicate POST returns 200 immediately. |
| Order persistence | `stripe_payment_intent_id` | `orders.stripe_payment_intent_id UNIQUE` — `place_order_from_cart` returns the existing order row instead of inserting again. |
| Prodigi order creation | `Idempotency-Key: wp-{order_id}` (HTTP header) | Prodigi de-dupes server-side; we additionally short-circuit in `create_prodigi_order_job` if `prodigi_orders.idempotency_key` already has `prodigi_order_id` set. |
| Refund | Refund row + `stripe_refund_id UNIQUE` | A second `request_refund()` call against an already-refunded order returns the existing succeeded Refund row. |

---

## Refund flow (Prodigi cancel quirks)

`refunds.service.request_refund(order_id, amount_cents, reason, requested_by_user_id)`:

1. Insert `refunds` row with `status='pending'` (audit anchor — even if the rest of the flow crashes, we have a record of intent).
2. **Best-effort Prodigi cancel.** Per `memory/project_prodigi_quirks.md`:
   * Prodigi has **no `/pause` endpoint** — only `cancel_order`.
   * `cancel_order` works only before `inProduction` becomes `InProgress`.
   * If Prodigi rejects with a 4xx ("already in production"), we record `prodigi_cancel_attempted=True, prodigi_cancel_succeeded=False` and **continue** with the Stripe refund. Customer keeps the print AND gets refunded — the margin loss is acceptable.
3. Issue Stripe refund via `stripe_client.create_refund`. Reason text is normalized to one of `{'duplicate', 'fraudulent', 'requested_by_customer'}`.
4. Persist `stripe_refund_id`, flip `refund.status='succeeded'` and `order.status='refunded'`.
5. Enqueue `email.refunded` outbox row (carries `prodigi_cancel_succeeded` so the template can adjust the wording).

---

## Outbox pattern (why)

Every external side effect (email, Prodigi, follow-up renders) is enqueued
via the `outbox` table inside the **same** SQLAlchemy transaction as the
business mutation. The webhook handler:

```
BEGIN
  INSERT stripe_events ...
  INSERT orders ...
  INSERT order_items ...
  INSERT outbox (kind='prodigi.create_order', payload={order_id}) ...
  INSERT outbox (kind='render.tier_3', payload=...) ...
  INSERT outbox (kind='email.order_confirmed', payload=...) ...
COMMIT
```

If any single statement fails the entire transaction rolls back — we
never end up with "charged the card but never sent the receipt" or
"placed the Prodigi order but lost the order row". The RQ worker drains
the outbox asynchronously (`drain_outbox_job` for emails, dedicated jobs
for `prodigi.create_order` and `render.tier_3`).

The `outbox` table tracks `attempts`, `next_retry_at` (exponential backoff:
1m → 5m → 25m → 2h → 10h), and a terminal `dead` status after 5 attempts.

---

## Legacy coexistence

The original $49 digital-unlock flow remains:

| Concern | Legacy path (kept) | New physical path (Phase 3b) |
|---|---|---|
| Checkout session create | `POST /api/create-checkout-session` (in `review_app.app`) | `POST /api/checkout/start` (new blueprint) |
| Webhook | `POST /webhook/stripe` (in `review_app.app`) | `POST /webhook/stripe/v2` (new blueprint) |
| Webhook secret | `STRIPE_WEBHOOK_SECRET` | `STRIPE_WEBHOOK_SECRET_V2` (falls back to `STRIPE_WEBHOOK_SECRET`) |
| Lead/order storage | `metadata/leads.json` (file) + Phase 3a synthetic `orders` rows from `0013_migrate_leads_json` | `customers` + `addresses` + `carts` + `orders` + `order_items` (relational) |

Per integration-plan.md decision #3: the digital download ships **bundled
free with the physical product** — we do not add a separate $49 line item
when a customer buys a physical print. Conceptually the print "comes with"
the digital file, in the box.

---

## Test / sandbox setup

### Environment variables

```
# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...           # legacy /webhook/stripe
STRIPE_WEBHOOK_SECRET_V2=whsec_...        # new /webhook/stripe/v2

# Prodigi
PRODIGI_ENV=sandbox                       # 'sandbox' | 'live'
PRODIGI_API_KEY_SANDBOX=...
PRODIGI_API_KEY_LIVE=...
PRODIGI_SHIPPING_METHOD=Standard          # default; Budget|Standard|StandardPlus|Express|Overnight

# Smarty (address validation)
SMARTY_AUTH_ID=...
SMARTY_AUTH_TOKEN=...

# DO Spaces (signed URLs we hand to Prodigi)
SPACES_ACCESS_KEY_ID=...
SPACES_SECRET_ACCESS_KEY=...
SPACES_REGION=nyc3
SPACES_ENDPOINT=https://nyc3.digitaloceanspaces.com

# App
PUBLIC_BASE_URL=https://wildlife.5story.com   # used to build Stripe success/cancel URLs
```

### Sending a test event with the Stripe CLI

```sh
stripe login
stripe listen --forward-to http://localhost:5000/webhook/stripe/v2
# in a second shell:
stripe trigger checkout.session.completed
```

### Running the test suite

```sh
pytest tests/cart/ tests/checkout/ tests/orders/ tests/refunds/ -v
```

All Phase 3b external integrations (Stripe SDK, Prodigi client,
DO Spaces) are mocked at the module boundary — the suite is hermetic
and runs against in-memory SQLite by default.

### End-to-end sandbox test (manual)

1. Start the app + RQ worker locally with `PRODIGI_ENV=sandbox`.
2. Open `/preview/<spec_hash>` (Phase 2 configurator).
3. Click Add to Cart → land on `/cart`.
4. POST to `/api/checkout/start` (use the Stripe test card `4242 4242 4242 4242`).
5. Watch:
   * `stripe_events` row inserted.
   * `orders` row inserted with `status='paid'`.
   * `outbox` rows with `kind in {prodigi.create_order, render.tier_3, email.order_confirmed}`.
   * Worker drains the outbox; eventually `orders.status` becomes `'in_production'`.
   * Sandbox Prodigi order visible at `https://api.sandbox.prodigi.com/v4.0/Orders/{ord_xxx}`.
