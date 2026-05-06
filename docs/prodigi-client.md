# Prodigi v4.0 client — Phase 1 runbook

This is the operations doc for `review_app/prodigi/`. It covers when to use
sandbox vs live, how the webhook re-fetch model works, idempotency, the
pause/cancel/refund window, error-code recovery, and the manual image
quality monitoring loop.

## Architecture overview

```
                    ┌──────────────────────────────────────────────┐
                    │                fishingposter.com               │
                    │                                                │
checkout.success ──►│ create_order(idempotency_key=order_uuid)       │
                    │   │                                            │
                    │   ▼                                            │
                    │ ProdigiClient (httpx, retry+backoff)           │
                    │   │                                            │
                    │   ▼                                            │
                    └───┼────────────────────────────────────────────┘
                        │  POST /v4.0/Orders                          ▲
                        │  ◀────────────────────────────────────────  │
                        │   Idempotency-Key header                    │
                        ▼                                              │
              api.{sandbox.}prodigi.com                                │
                        │                                              │
                        │  callback (CloudEvents)                      │
                        ▼                                              │
                    ┌───┴────────────────────────────────────────────┐ │
                    │ POST /webhook/prodigi                          │ │
                    │   ├─ insert prodigi_callbacks (UNIQUE event_id)│ │
                    │   ├─ enqueue process_callback_job              │ │
                    │   └─ return 200 OK                             │ │
                    │                                                │ │
                    │ process_callback_job(callback_id)              │ │
                    │   ├─ GET /v4.0/Orders/{id}  ────────────────────┤
                    │   ├─ upsert prodigi_orders                     │
                    │   ├─ upsert shipments                          │
                    │   └─ TODO email via outbox (Phase 3)           │
                    └────────────────────────────────────────────────┘
```

### Sandbox vs live

| Environment | Base URL                          | Env var                        | When to use |
|-------------|-----------------------------------|--------------------------------|-------------|
| Sandbox     | `https://api.sandbox.prodigi.com` | `PRODIGI_API_KEY_SANDBOX`      | Dev, CI, staging, integration tests, every E2E rehearsal |
| Live        | `https://api.prodigi.com`         | `PRODIGI_API_KEY_LIVE`         | Production order placement only |

`get_default_client()` picks based on `PRODIGI_ENV` (`sandbox` default; set
to `live` in production secrets). Mixing is not allowed: a sandbox order
ID is meaningless to the live API and vice versa.

### Wiring the webhook into Flask

The Phase 1 client deliberately does **not** modify `review_app/app.py`.
To enable the webhook in Phase 2 (or anywhere downstream), add the
following to the wiring section of `app.py` — alongside the existing
`init_obs / init_db / init_auth` calls:

```python
from review_app.prodigi import init_app as _init_prodigi
_init_prodigi(app)
```

Set `PRODIGI_ENV=sandbox` (default) in dev and `PRODIGI_ENV=live` in
production. The webhook URL is `https://<host>/webhook/prodigi`. Configure
this in your Prodigi dashboard under Settings > Integrations > Callbacks.

## Webhook flow: CloudEvents → re-fetch → mutate

Prodigi callbacks are CloudEvents v1.0 envelopes. **Per Prodigi docs they
are not signed**, so there's no HMAC to verify; instead the integration
contract is "trust nothing in the body except `event_id`."

The receiver does three things:

1. **Dedupe by `event_id`.** The `prodigi_callbacks` table has a unique
   index on `event_id`. A duplicate insert is silently ignored — the
   second POST returns `{"status": "duplicate"}` with HTTP 200.

2. **Always 200 OK quickly.** All meaningful work happens in
   `process_callback_job(callback_id)`, which runs in the RQ queue (or
   inline in tests via `PRODIGI_WEBHOOK_INLINE=1`). The HTTP handler only
   parses the envelope and inserts the dedupe row.

3. **Re-fetch before mutating local state.** The job calls
   `client.get_order(prodigi_order_id)` and uses *that* response as the
   source of truth — never the body of the callback. This means even if
   Prodigi sends a malformed or stale event, our local `prodigi_orders`
   row stays consistent with `/v4.0/Orders/{id}`.

This is the documented Prodigi pattern. It means an attacker who guesses
our webhook URL can at most:

* trigger a useless re-fetch (rate-limited by `event_id` dedupe),
* insert callbacks with `processed_status='ignored'` if they fabricate
  event IDs that don't have a real `prodigi_order_id`.

Neither leaks data nor mutates state.

## Idempotency strategy

We send an `Idempotency-Key` HTTP header on every POST /Orders. The
canonical key is the **fishingposter order UUID** (when Phase 3 lands)
or a random `wildprint-{uuid4}` for the Phase 1 sandbox tests.

Per Prodigi docs:

> Repeating the same key returns the existing order with outcome
> `alreadyExists` rather than creating a new one.

So a retry of `create_order` after a network blip never double-charges.
We store the key in `prodigi_orders.idempotency_key` (UNIQUE) so we can
reconstruct the relationship from our side too.

If the network call fails *before* we recorded the key, the next attempt
generates a new key. That's acceptable — the worst case is a single
duplicate order, surfaced by Phase 4 admin tooling (the
`prodigi_orders` rows differ in idempotency_key but share a
`fishingposter_order_id`, which the admin UI can flag).

## Pause / cancel / refund coordination

Prodigi v4.0 does **not** expose a `/pause` endpoint per-order. Instead
the dashboard configures a global pause window for *all* orders (e.g.
"hold every order for 15 minutes before submitting to the lab"). Our
integration assumes this is configured to a value that's longer than our
"customer hits the cancel button on the success page" timeout.

The cancel flow is:

1. Customer requests cancel → we call
   `ProdigiClient.cancel_order(prodigi_order_id)`.
2. Outcome `Cancelled` → refund the full amount.
3. Outcome anything else → fall through to support; the order is past
   the cancel window and we have to refund only shipping per Prodigi's
   T&Cs.

For now this logic lives in the Phase 3 admin tooling. Phase 1 just
exposes the client method and treats the response as data.

## Common error codes and recovery

| `errorCode`                                | Meaning                                            | Recovery action |
|--------------------------------------------|----------------------------------------------------|-----------------|
| `order.items.assets.NotDownloaded`         | One asset failed a download attempt; will retry    | Watch — Prodigi retries up to 10 times automatically |
| `order.items.assets.FailedToDownloaded`    | All 10 download attempts failed                    | Manual intervention: re-upload or change URL via support; track in admin queue |
| `order.items.ItemUnavailable`              | SKU temporarily unavailable                        | Auto-retry quote refresh in 24 h; notify customer via support if still unavailable in 48 h |
| `destinationCountryCode.UsSalesTaxWarning` | Quote may be subject to US sales tax               | Informational only; Stripe handles tax separately |

Surfaced as 4xx HTTP responses (we map to `ProdigiClientError`) or as
`Issue` entries inside the `Order.status.issues` array (we copy these
into `prodigi_orders.status_details` for admin visibility).

## Image-quality rejection — manual loop

Prodigi reserves the right to reject low-resolution images at the lab,
but **does not provide a programmatic notification**. Per their docs and
their support team:

> If an image fails our quality bar after the order has been submitted,
> our team contacts the merchant via email at the address registered on
> the account.

This means:

* In production, the email address `prodigi@5story.com` (or similar
  ops alias) must be checked daily.
* Phase 4 admin builds a "rejected by Prodigi" queue that operators
  manually populate from the support email thread.
* No automation here in Phase 1 — flagged in `docs/integration-plan.md`
  as a known gap.

The defensive fix is to render at full 7200×10800 @ 300 DPI before
submission (Tier-3 render; see `docs/integration-plan.md` decision #5)
so the image is genuinely above Prodigi's minimum DPI for our launch
SKUs (24×36" at 300 DPI = 7200×10800).

## Quote refresh job

`refresh_all_skus_job()` is the entry point. It:

1. Selects every `prodigi_skus` row where `active=true`.
2. POSTs `/v4.0/Quotes` for a single-item order (1 copy, US Standard
   shipping, the SKU's color attribute).
3. Picks the cheapest quote across returned shipping methods.
4. Writes back `last_quoted_wholesale_cents`, `last_refreshed_at`, and
   recomputes `margin_cents` if `retail_price_cents` is set.

Failures per SKU are logged but never abort the batch. The summary dict
returned by the job is `{"checked": N, "succeeded": M, "failed": K}`.

Phase 5 adds RQ-Scheduler to run this nightly at 03:00 UTC.

## Observability

All client / webhook / quote-refresh code uses `structlog` with the
logger names:

* `prodigi.client` — every HTTP request/response
* `prodigi.webhooks` — webhook receive + processing
* `prodigi.quote_refresh` — per-SKU refresh outcomes

Each log line includes (when applicable): `request_id`, `endpoint`,
`status`, `latency_ms`, `prodigi_outcome`, `event_id`,
`prodigi_order_id`, `internal_sku`. Production ships these to whatever
log aggregator the observability stack points at.
