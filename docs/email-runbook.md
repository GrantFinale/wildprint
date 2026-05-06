# Email runbook (Phase 0.5)

Owner: Grant. Last updated: 2026-05-05.

## Architecture

Transactional email is wired through a **transactional outbox**. Every send
is two database writes (business mutation + outbox row) inside one
transaction; a separate worker drains the outbox into Resend.

```
[ request handler ]
     |
     |  email.send(session, kind, to, payload)
     v
+---------------+      commit (atomic)
|  outbox row   |  ---------------------->  durable
|  status=pending|
+---------------+
     |
     |  drain_outbox_job (RQ, every 30s)
     v
[ Resend API ]  ---->  inbox
     |
     |  on success: status=sent, sent_at=now, payload.resend_message_id=...
     |  on failure: attempts++, status=failed, next_retry_at += backoff
     |              (or status=dead after max_attempts)
```

**Why outbox:** the Stripe webhook handler must do "mark order paid" +
"create Prodigi order" + "send confirmation email" atomically from the
customer's perspective. With a true atomic DB transaction wrapping the row
write plus an outbox enqueue, the worker can fan out without risking the
"we charged the card but never sent the receipt" failure mode.

**Why never send from a request handler:** an HTTP timeout, a retry, or a
crashed Resend would each silently double-send or skip-send if email lived
in the request path. The outbox makes idempotency a property of the row
transitions instead.

## Components

| File | Role |
| --- | --- |
| `alembic/versions/0004_outbox.py` | Schema migration. |
| `review_app/email/__init__.py` | Public API: `send()`, `render_template()`, `init_app()`. |
| `review_app/email/outbox.py` | `OutboxEntry` model + `enqueue` / `claim_batch` / `mark_sent` / `mark_failed`. |
| `review_app/email/resend_client.py` | Raw HTTP wrapper around Resend's `/emails`. |
| `review_app/email/templates.py` | `KIND_TO_TEMPLATE` registry + Jinja2 renderer. |
| `review_app/email/templates/*.j2` | HTML + text bodies, one pair per kind. |
| `review_app/queue/jobs.py::drain_outbox_job` | Worker entry point. |
| `scripts/smoke_email.py` | Manual end-to-end smoke check. |

## How to send an email from app code

```python
from review_app.email import send

# Inside a request handler that already holds a SQLAlchemy session:
send(
    session,
    kind="email.order_confirmed",
    to=customer.email,
    payload={
        "order_number": order.number,
        "customer_name": customer.display_name,
        "line_items": [
            {
                "name": item.name,
                "size": item.size,
                "quantity": item.quantity,
                "price_cents": item.price_cents,
            }
            for item in order.items
        ],
        "total_cents": order.total_cents,
        "currency": order.currency,
    },
)
session.commit()  # outbox row + business mutation become durable together
```

Do **not** import `resend_client.send_via_resend` from a request handler.
That function is for the worker only (and the smoke script).

## How to add a new kind

1. Decide on a kind string. Convention: `email.<noun_phrase>` (snake_case).
2. Drop `<filename>.html.j2` and `<filename>.txt.j2` in
   `review_app/email/templates/`. Use `StrictUndefined`-safe Jinja
   (every variable referenced must appear in the payload, or be defaulted
   with `|default(...)`).
3. Register the kind in `KIND_TO_TEMPLATE` in
   `review_app/email/templates.py`:
   ```python
   "email.<noun_phrase>": (
       "<subject jinja>",
       "<filename>.html.j2",
       "<filename>.txt.j2",
   ),
   ```
4. Add a render test in `tests/email/test_email.py` that exercises a
   representative payload and asserts on key strings.
5. Document the kind in this file's "Kinds" section below.

## Kinds (Phase 0)

| Kind | Status | Required payload keys |
| --- | --- | --- |
| `email.order_confirmed` | Real copy | `order_number`, `customer_name`, `line_items` (list of `{name, size, quantity, price_cents}`), `total_cents`, optional `currency` |
| `email.shipped` | Real copy | `order_number`, `carrier`, `tracking_number`, optional `tracking_url` |
| `email.in_production` | Stub (Phase 3) | `order_number` |
| `email.delivered` | Stub (Phase 3) | `order_number` |
| `email.refunded` | Stub (Phase 3) | `order_number`, optional `amount_cents` |
| `email.problem` | Stub (Phase 3) | `order_number`, optional `message` |

Phase 3 is the customer-comms polish phase that fills in real copy +
brand styling for the stubs.

## Retrying stuck or failed entries

The worker handles `status='failed'` rows automatically — they get retried
on the backoff schedule (1m, 5m, 25m, 2h, 10h). No manual action needed.

`status='dead'` rows are terminal: the worker will not touch them. To
manually un-dead a row (e.g. after fixing a template bug):

```sql
-- Reset one specific row.
UPDATE outbox
   SET status        = 'pending',
       attempts      = 0,
       next_retry_at = now(),
       last_error    = NULL,
       updated_at    = now()
 WHERE id = $1
   AND status = 'dead';
```

```sql
-- Reset all dead rows of a given kind, e.g. after a template hotfix.
UPDATE outbox
   SET status        = 'pending',
       attempts      = 0,
       next_retry_at = now(),
       last_error    = NULL,
       updated_at    = now()
 WHERE status = 'dead'
   AND kind = 'email.order_confirmed';
```

```sql
-- Inspect the death pile.
SELECT id, kind, attempts, last_error, updated_at
  FROM outbox
 WHERE status = 'dead'
 ORDER BY updated_at DESC
 LIMIT 50;
```

A proper admin UI for replaying dead-letter rows is on the Phase 4
backlog.

## Inspecting the outbox

```sql
-- Pending backlog
SELECT kind, count(*) FROM outbox
 WHERE status IN ('pending', 'failed')
   AND next_retry_at <= now()
 GROUP BY kind;

-- Recent activity by kind
SELECT id, status, attempts, created_at, sent_at
  FROM outbox
 WHERE kind = 'email.order_confirmed'
 ORDER BY created_at DESC
 LIMIT 20;

-- Find the Resend message id for a sent row
SELECT payload->>'resend_message_id'
  FROM outbox
 WHERE id = $1;
```

## Rate limits

Resend free tier: **100 emails/day, 3,000 emails/month**.

We will outgrow this once Phase 3 launches. Triggers to upgrade:

- **First**: any day where the outbox sends >50 (50% of daily cap) — sign
  up for the Pro plan ($20/mo, 50k/mo) before we hit the wall.
- **Hard upgrade**: any day where Resend returns 429 — same-hour upgrade.

The Pro plan removes the daily cap and triples the monthly. Long-term
(Phase 5+ if volume justifies), Resend Scale or AWS SES are the next steps.

## Smoke test

After any credential rotation, deploy of the email module, or domain DNS
change:

```bash
RESEND_API_KEY=re_... EMAIL_FROM=hello@fishingposter.com \
    python scripts/smoke_email.py benedictmt@gmail.com
```

Expected: `OK — Resend message id: <msg_xxx>` printed; email arrives in
benedictmt@gmail.com inbox within ~30s.

If the email doesn't arrive:
1. Check Resend dashboard "Logs" for the message id — look at status,
   delivery events, bounce reason.
2. Verify domain is still "Verified" in Resend dashboard
   (`fishingposter.com`).
3. `dig TXT fishingposter.com` to confirm SPF/DMARC TXT records didn't
   get wiped by a DNS edit.
4. `dig CNAME resend._domainkey.fishingposter.com` to confirm DKIM CNAME
   still resolves.

## Env vars

| Var | Required | Notes |
| --- | --- | --- |
| `RESEND_API_KEY` | At send time | Send-only scope is sufficient. Read at first send, not at import. |
| `EMAIL_FROM` | At send time | E.g. `hello@fishingposter.com`. |
| `DATABASE_URL` | Always | Outbox rows live in the main app DB. |
| `REDIS_URL` | For the worker | RQ uses this; the email module itself doesn't. |
