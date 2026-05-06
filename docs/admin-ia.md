# fishingposter.com — Admin Information Architecture

**Phase 4a deliverable.** Sign-off required before any code (Phase 4b shell, 4c Prodigi pages).
**Date:** 2026-05-05  ·  **Author:** wildprint
**Constraints:** sidebar nav, max 2 levels, RBAC (admin/staff/viewer), Prodigi env indicator on all fulfillment pages, mobile-no-break.

---

## 1. Top-level nav (sidebar tree)

```
Admin
├── Dashboard                                         (1 route)
├── Orders                                            (4 routes)
│   ├── All orders                  (?status= filters open|production|shipped|refunded|problem)
│   ├── Order detail                (/orders/<id>)
│   ├── Refunds queue               (filtered list + processing modal)
│   └── Test orders                 (sandbox-routed creation tool)
├── Customers                                         (2 routes)
│   ├── All customers
│   └── Customer detail             (/customers/<id> → orders, addresses, emails, notes)
├── Catalog                                           (6 routes)
│   ├── Species                     (existing admin table, search/filter/missing-masters)
│   ├── Backgrounds                 (existing gallery + Flux Pro Ultra generator)
│   ├── Sizing                      (global variance + per-species scale)
│   ├── Frame SKUs & pricing        (32 SKUs, margin, nightly Quote refresh status)
│   ├── Lakes                       (lake-name dictionary, region tagging)
│   └── Render presets              (three-tier resolution defaults, watermark settings)
├── Fulfillment                                       (4 routes)
│   ├── Connection                  (Prodigi API key, sandbox/prod toggle, callback URL)
│   ├── Webhook log                 (CloudEvents feed + retry queue for failed callbacks)
│   ├── Error queue                 (Prodigi rejections, address issues, image-quality)
│   └── Reprints                    (manual reorder against existing print asset)
├── Content                                           (3 routes)
│   ├── Email templates             (order_confirmed / in_production / shipped / delivered / refunded / problem)
│   ├── Email send log              (Resend deliveries, bounces, opens)
│   └── Marketing pages             (homepage hero, about, FAQ — Jinja blocks)
├── Analytics                                         (3 routes)
│   ├── Sales                       (revenue, AOV, conversion, top SKUs)
│   ├── AI usage                    (per-provider cost, token/unit breakdown, top render_specs)
│   └── Operations                  (fulfillment SLA, error rate, reprint rate)
└── Settings                                          (5 routes)
    ├── Users & roles               (admin/staff/viewer assignment, invites)
    ├── API keys                    (Stripe, Resend, Prodigi, Smarty, OpenAI, Recraft, Replicate)
    ├── Integrations                (per-service health check + last-call timestamp)
    ├── Audit log                   (who/what/when, filterable)
    └── My account                  (password, 2FA, API tokens for self)
```

8 categories · ~28 routes. Depth = 2 (category → page). Order detail / customer detail are deep links, not sidebar entries.

---

## 2. Page-by-page IA

| Page | Purpose | Primary actions | Columns / fields | Filters / search | Roles |
|---|---|---|---|---|---|
| **Dashboard** | At-a-glance health | Drill-through cards | Today's orders, revenue, in-production, shipped 7d, error count, AI spend MTD, top SKU | Date range | all (read) |
| **Orders / All** | Operational triage | Open detail, bulk export | Order #, customer, SKU summary, total, status badge, age, Prodigi `ord_*` | status, date, SKU, customer, problem-only | admin/staff R+W, viewer R |
| **Order detail** | Single source of truth per order | Refund, cancel, pause, reprint, resend email, edit address (pre-production), add note | Customer, line items, payment (Stripe), Prodigi status timeline, raw event log (collapsible JSON), shipping events, emails sent | — | admin/staff R+W, viewer R |
| **Orders / Refunds** | Process refunds end-to-end | Initiate Stripe refund + Prodigi cancel/pause | Order, amount, reason, status (pending/done/failed) | status | admin only |
| **Orders / Test** | Sandbox dogfood | Create test order routed to Prodigi sandbox, skip Stripe charge | SKU picker, address, render_spec | — | admin/staff |
| **Customers / All** | Lookup & lifetime view | Open detail | Email, name, total orders, LTV, last order, flag | search (email/name), value buckets | admin/staff R+W, viewer R |
| **Customer detail** | Per-customer history | Add note, send email, ban | Profile, addresses, orders table, email log, notes | — | admin/staff R+W, viewer R |
| **Catalog / Species** | Existing species mgmt | Edit scale, regenerate master | Thumb, name, category, regions, scale, scientific, has_master | search, category, missing-masters-only | admin/staff R+W, viewer R |
| **Catalog / Backgrounds** | Existing gallery + generation | Generate (Flux Pro Ultra → Real-ESRGAN 4x), pick preset, delete | Thumb grid, prompt, aspect | preset picker | admin/staff R+W |
| **Catalog / Sizing** | Existing global + per-species | Save variance, save per-species scale | Slider 0–2, table override | — | admin only |
| **Catalog / Frame SKUs** | Prodigi pricing manager | Edit retail, force re-quote, toggle active | Internal SKU, Prodigi SKU, size, finish, wholesale, retail, margin %, last quote ts | size, finish, active, low-margin | admin only |
| **Catalog / Lakes** | Lake dictionary | Add, merge, retire | Name, state, aliases, order count | search, state | admin/staff |
| **Catalog / Render presets** | Three-tier defaults | Edit dimensions, watermark text/opacity | Tier, px, DPI, watermark | — | admin only |
| **Fulfillment / Connection** | Prodigi config | Save key, switch env, rotate callback URL, send ping | API key (masked), sandbox/prod toggle, callback URL, last successful call | — | admin only |
| **Fulfillment / Webhook log** | Inspect callbacks, retry | Replay event, mark resolved | Event ID, type (created/shipments/completed), order #, received_at, dedupe hit, handler status | type, status, date | admin/staff R+W, viewer R |
| **Fulfillment / Error queue** | Recover failed orders | Retry, reassign, refund, contact customer | Order, error class (rejection/address/image-quality), Prodigi message, age | error class, age | admin/staff |
| **Fulfillment / Reprints** | Manual reprint | Create reprint at cost | Original order, reason, new Prodigi order #, status | reason | admin only |
| **Content / Email templates** | Edit transactional emails | Edit subject + MJML/HTML, send test | Template name, last edited by, last sent count | — | admin only |
| **Content / Email send log** | Resend delivery audit | Resend, view payload | Recipient, template, status (sent/bounce/complaint), opened, sent_at | template, status, date | admin/staff R, viewer R |
| **Content / Marketing pages** | Edit Jinja blocks | Save, preview | Page, last edited | — | admin only |
| **Analytics / Sales** | Revenue health | Date range, export CSV | Revenue, orders, AOV, conversion, top SKUs, refund rate | date, SKU | admin/staff/viewer R |
| **Analytics / AI usage** | Cost control | Drill into spec | Provider, units/tokens, cost, daily/weekly/monthly chart, top 10 render_specs by cost | provider, date, render_spec | admin/staff/viewer R |
| **Analytics / Operations** | Fulfillment SLA | — | Avg time-to-production, time-to-ship, error %, reprint % | date | admin/staff/viewer R |
| **Settings / Users & roles** | RBAC mgmt | Invite, change role, deactivate | Email, role, last login, 2FA on | role | admin only |
| **Settings / API keys** | Secret rotation | Reveal, rotate, save | Service, key (masked), last rotated, last used | — | admin only |
| **Settings / Integrations** | Health dashboard | Run check, view last response | Service, status dot, last call, error rate 24h | — | admin/staff R |
| **Settings / Audit log** | Compliance trail | Export | Actor, action, target, before/after diff, timestamp, IP | actor, action, date | admin only |
| **Settings / My account** | Self-service | Change password, set up 2FA, generate token | — | — | all |

---

## 3. Cross-cutting concerns

**Topbar (every page):**
`[logo] [breadcrumbs ......................] [global search] [Prodigi env: SANDBOX|PROD] [notifications 🔔] [user menu]`

- **Global search** — single input, scoped types: orders (#, email, Prodigi id), customers (email/name), species (name/scientific), SKU. `cmd+k` opens it.
- **Breadcrumbs** — `Admin / <Category> / <Page> [/ <Entity>]`. Always clickable up the chain.
- **Notifications bell** — counts unresolved fulfillment errors, failed webhook retries, low-margin SKU alerts. Click → drawer listing recent events with deep links.
- **Prodigi env indicator** — pill in topbar; **red "SANDBOX"** when key is sandbox, **green "PROD"** when live. Visible globally so a staffer working in Catalog still knows which env Order tests would hit.

**Role × capability matrix**

| Capability | admin | staff | viewer |
|---|---|---|---|
| View orders / customers / catalog | yes | yes | yes |
| Process refunds, cancels, reprints | yes | yes | no |
| Edit catalog (species, lakes, backgrounds) | yes | yes | no |
| Edit pricing / SKUs / render presets | yes | no | no |
| Generate AI assets (backgrounds) | yes | yes | no |
| Send test orders | yes | yes | no |
| Edit email templates / marketing pages | yes | no | no |
| Resend transactional email | yes | yes | no |
| Edit Prodigi connection / API keys / integrations | yes | no | no |
| Manage users & roles | yes | no | no |
| View audit log | yes | no | no |
| View analytics | yes | yes | yes |

Sidebar items the current role can't access are **hidden**, not greyed out. Direct-URL access returns 403.

---

## 4. Migration mapping (existing → new)

| Existing | New home |
|---|---|
| `GET /admin` species table | **Catalog / Species** |
| `GET /admin/data` JSON | Internal endpoint behind Catalog / Species (unchanged) |
| `POST /admin/settings/global_size_variance` | **Catalog / Sizing** (global slider) |
| `POST /admin/species/<slug>/scale` | **Catalog / Sizing** (per-species table) and **Catalog / Species** (inline edit) |
| Background gallery + Flux Pro Ultra + Real-ESRGAN 4x | **Catalog / Backgrounds** |
| Coverage cards (all-species count, coverage %, missing masters) | **Dashboard** (header strip) and **Catalog / Species** (summary above table) |
| Search / category filter / missing-masters checkbox | **Catalog / Species** (filter row, unchanged behavior) |
| (no auth) | gated by **Settings / Users & roles** — Phase 0 dependency |

Nothing existing is dropped. Behavior preserved exactly during 4b shell migration; only chrome changes.

---

## 5. NOT in admin

- Customer-facing **order history page** (`/account/orders`) — lives in the storefront.
- Public **marketing pages** (homepage, about, FAQ) at root — admin only edits the content blocks via Content / Marketing pages; the rendered pages are storefront.
- **Configurator / preview** — storefront.
- **Stripe webhook receiver** — backend service endpoint, not a UI page (its events surface in Order detail).
- **Prodigi callback receiver** — backend endpoint; surfaces in Fulfillment / Webhook log.
- **Sentry / log viewer** — external (Sentry UI). Admin links out, doesn't reimplement.
- **Cron job runner** (nightly Quote refresh) — backend; status surfaces on Catalog / Frame SKUs as `last quote ts`.

---

## 6. Low-fi wireframes

### 6a. Orders / All

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ logo  Admin / Orders                       [search] [SANDBOX] [🔔3] [grant ▾] │
├──────────┬────────────────────────────────────────────────────────────────────┤
│ Dash     │ Orders                                                             │
│ ▸ Orders │ [All] [Open] [In production] [Shipped] [Refunded] [Problem 3]     │
│ Custom.. │ ┌──────────────────────────────────────────────────────────────┐  │
│ Catalog  │ │ search ▢   date ▢   SKU ▢   customer ▢       export CSV ▢   │  │
│ Fulfill. │ ├──────────────────────────────────────────────────────────────┤  │
│ Content  │ │ # 1083  jane@…  16x24 Black ×1   $89  ⬤ in_prod  2h  ord_…  │  │
│ Analyt.. │ │ # 1082  bob@…   24x36 Natural ×1 $129 ⬤ shipped  1d  ord_…  │  │
│ Settings │ │ # 1081  amy@…   12x16 White ×2   $98  ◯ open     3h  —      │  │
│          │ │ # 1080  ...                                                   │  │
│          │ └──────────────────────────────────────────────────────────────┘  │
│          │                                            ‹ 1 2 3 4 5 ... 12 ›   │
└──────────┴────────────────────────────────────────────────────────────────────┘
```

### 6b. Order detail

```
Admin / Orders / #1083                                  [SANDBOX] [🔔]  [grant ▾]
┌──────────────────────────────────────────────────┬─────────────────────────────┐
│ Order #1083                  [Refund] [Reprint]  │ Customer                    │
│ jane@example.com · placed 2h ago                 │ Jane Doe                    │
│                                                   │ jane@example.com            │
│ ── Line items ─────────────────────────────────── │ 1 prior order · LTV $218    │
│ 16×24 Black Frame · Lake Tahoe · Brown Trout     │ ── Shipping address ──      │
│ render_spec=rs_… preview ▢   $89.00              │ 123 Main St                 │
│                                                   │ Reno, NV 89501              │
│ ── Payment (Stripe) ───────────────────────────── │ [edit · pre-production]     │
│ pi_3O… · Visa •4242 · captured $89.00            │                             │
│                                                   │ ── Notes ──                 │
│ ── Prodigi timeline ──────────────────────────── │ + add note                  │
│ ⬤ created       Prodigi ord_abc123 · 2h ago      │                             │
│ ⬤ in_production                  · 45m ago       │ ── Emails sent ──           │
│ ◯ shipped                                         │ • order_confirmed (open)    │
│ ◯ delivered                                       │ • in_production (sent)      │
│                                                   │   [resend ▾]                │
│ ▸ Raw event log (3 events) ────────────────────  │                             │
└──────────────────────────────────────────────────┴─────────────────────────────┘
```

### 6c. Catalog / Frame SKUs

```
Admin / Catalog / Frame SKUs                           [PROD] [🔔]  [grant ▾]
┌──────────┬─────────────────────────────────────────────────────────────────────┐
│ sidebar  │ Frame SKUs & pricing                          [Refresh all quotes]  │
│          │ size ▢ finish ▢ active ▢  ☐ low-margin only                         │
│          │ ┌───────────────────────────────────────────────────────────────┐   │
│          │ │ Internal · Prodigi SKU · Size · Finish    · Wsale · Retail · M%│  │
│          │ │ cf_12_blk · GLOBAL-CFP-… · 12×16 · Black  · $14.20 · $49 · 71%│   │
│          │ │ cf_16_blk · GLOBAL-CFP-… · 16×24 · Black  · $22.10 · $89 · 75%│   │
│          │ │ cf_16_nat · GLOBAL-CFP-… · 16×24 · Natural· $22.10 · $89 · 75%│   │
│          │ │ cf_24_agd · GLOBAL-CFP-… · 24×36 · A.Gold · $48.30 · $129· 63%│   │
│          │ │   ...  (32 rows total, 4 sizes × 8 finishes)                   │   │
│          │ └───────────────────────────────────────────────────────────────┘   │
│          │ Last nightly refresh: 2026-05-05 03:00 UTC · 32/32 succeeded       │
└──────────┴─────────────────────────────────────────────────────────────────────┘
```

### 6d. Analytics / AI usage

```
Admin / Analytics / AI usage                           [PROD] [🔔]  [grant ▾]
┌──────────┬─────────────────────────────────────────────────────────────────────┐
│ sidebar  │ Range: [last 30d ▾]   Provider: [all ▾]                             │
│          │ ┌───── MTD spend ────┬──── Today ─────┬──── Yesterday ─────┐        │
│          │ │  $182.40           │  $7.10         │  $9.80             │        │
│          │ └────────────────────┴────────────────┴────────────────────┘        │
│          │ ┌─────────── daily cost (stacked, by provider) ───────────┐        │
│          │ │  ▁▂▃▅▆▄▃▆▇█▆▅▄▃▂▃▅▆ ...                                  │        │
│          │ └──────────────────────────────────────────────────────────┘        │
│          │ Per provider                                                         │
│          │  OpenAI    $84.10  · 1.2M tokens                                     │
│          │  Recraft   $61.20  · 408 units                                       │
│          │  Replicate $37.10  · 91 Flux runs · 91 ESRGAN runs                   │
│          │ Top render_specs by cost (last 30d)                                 │
│          │  rs_a1b2 · Brown Trout / Tahoe / vintage_engraving · $14.30 · 47×    │
│          │  rs_…    · ...                                                       │
└──────────┴─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Open IA decisions for Grant to confirm

1. **Refunds: standalone page + inside Order detail** (chose **both**). Picked because operators triage refund queues by status, but most refunds are initiated from a specific order. Alt: detail-only would force a saved-search workflow for batch refunds.
2. **Sizing: own Catalog page vs inline on Species** (chose **own page**, with per-species scale also editable inline on Species). Picked because the global variance slider is a power-user knob that benefits from explanation real estate; collapsing it into Species hides the global lever. Alt: Species-only would shrink nav by one item but bury the global slider.
3. **Test orders: under Orders vs under Settings** (chose **Orders / Test**). Picked because the mental model is "make a fake order"; settings is for configuration, not actions. Alt: under Settings keeps actionable Orders pages clean but hides the tool from staff.
4. **Reprints: Fulfillment vs Order detail action** (chose **both**, page is the queue, action lives on order). Same logic as refunds — operators want both the per-order action and the cross-order list.
5. **AI usage under Analytics vs Settings** (chose **Analytics**). Picked because it answers "what are we spending" not "how is AI configured." Provider keys live in Settings / API keys; spend lives in Analytics. Alt: Settings would couple cost-watching with key rotation, which conflates audiences (CFO vs sysadmin).

Bonus flag: **Catalog / Lakes** is new (not in the brief's category list). Added because lake names are a first-class catalog entity (already used in checkout metadata, will be used in product titles and SEO). If we leave it loose in `leads.json` it becomes garbage. Confirm OK to add.

---

**Sign-off:** approve to proceed with Phase 4b shell. Edits welcome inline.
