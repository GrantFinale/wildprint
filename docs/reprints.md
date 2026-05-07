# Reprint workflow — Phase 5b

## Two flavors

1. **Customer-initiated** — the customer hits "Request a reprint" on
   their order detail page (`/account/orders/<id>`). Eligibility:
   - Order status is `delivered`.
   - `delivered_at` is within the last 30 days.
   - No existing pending/approved/completed reprint for the order.

2. **Admin-initiated** — the admin POSTs to
   `/admin/orders/<id>/reprint` from the order detail page. No
   eligibility check beyond "the order exists" (admins know best).

Both paths funnel through `review_app.refunds.reprints.request_reprint(...)`
which inserts a row in `reprint_requests` with `status='pending'` and
emails ops via `email.admin_reprint_requested` outbox.

## Admin processing

`/admin/fulfillment/reprints` shows the queue. For each pending row:

- **Approve** -> `approve_reprint(...)` creates a NEW Order with
  `source='reprint'`, `total_cents=0`, snapshots the original line items
  + shipping address, and stamps the reprint row with
  `new_prodigi_order_id`. The actual Prodigi order creation rides the
  existing `prodigi.create_order` outbox pipeline (same path as a paid
  order).
- **Reject** -> `reject_reprint(...)` sets `status='rejected'`, records
  the admin's reason, and emails the customer via
  `email.reprint_rejected`.

## Cost accounting

Reprints are FREE to the customer. Internal cost (what we pay Prodigi)
is captured in `orders.internal_cost_cents` on the new reprint Order.

Phase 4b shoehorned reprint cost into `orders.tax_cents`; migration
`0021_reprint_cost.py` moves those values to `internal_cost_cents` and
zeroes the misused tax_cents on `source='reprint'` rows. Once Stripe
Tax (Phase 5a) lands, real tax values can flow into `tax_cents` without
collision.

## Operations metric

Migration `0020_orders_production_ts.py` adds
`orders.in_production_at`. The Prodigi callback handler
(`review_app.orders.jobs`) now stamps this column when an order
transitions to `in_production`, replacing the Phase 4b
`avg_ttship * 0.4` placeholder in the operations analytics page.

## Prodigi caveats

Per `memory/project_prodigi_quirks.md`:

- Prodigi has **no `/pause` endpoint**. We can only cancel before the
  `inProduction` stage flips to `InProgress` — a tiny window.
- Refunds + reprints can therefore co-exist: if a Prodigi cancel
  fails ("already in production"), the customer keeps the original
  print AND we issue the reprint as a goodwill gesture. Margin loss is
  acceptable per Phase 3b decision.
