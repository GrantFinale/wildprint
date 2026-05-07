# Customer account (/account/*) — Phase 5b

## Overview

The customer-facing storefront has its own minimal Flask blueprint mounted
at `/account`, separate from the admin shell at `/admin`. Customers
authenticate via **magic link** rather than a password — the friction of
remembering yet-another password isn't worth it for a low-traffic
e-commerce site.

## Auth flow

1. Customer enters email at `GET /account/login`.
2. `POST /account/login` looks up (or creates) a `customers` row, signs a
   short-lived JWT, and stores its SHA-256 hash in `customer_login_tokens`.
   The link is enqueued via the outbox/Resend pipeline using kind
   `email.account_magic_link`.
3. Clicking the email link hits `GET /account/login/verify?token=…`. The
   handler decodes the JWT, looks up the row by token hash, marks
   `used_at = now()`, and sets `session['customer_id']`.
4. Subsequent requests inside the `/account` blueprint look up the
   customer in a `before_request` hook and populate `g.current_customer`.
   The `@requires_customer` decorator redirects to `/account/login?next=…`
   when no customer is on the session.

Token TTL: **15 minutes**. Tokens are single-use.

## Why a separate session key?

`session['customer_id']` does **not** collide with the existing admin
auth (`flask_login` cookie) or with the legacy `unlocked` cookie used by
the $49 unlock flow. A customer can be signed into both simultaneously
(admins testing the storefront) without confusing either system.

## Routes

| Path | Purpose |
| --- | --- |
| `GET /account/` | Overview — recent orders + default address |
| `GET /account/orders` | All orders for the signed-in customer |
| `GET /account/orders/<id>` | Order detail, status timeline, reprint button |
| `POST /account/orders/<id>/reprint` | Customer-initiated reprint request |
| `GET /account/addresses` | Address book (CRUD via inline forms) |
| `GET /account/profile` | Edit name, email, marketing opt-in |
| `GET /account/login` | Magic-link login form |
| `POST /account/login` | Issue magic link |
| `GET /account/login/verify` | Verify token, set session |
| `GET /account/logout` | Clear session, redirect to login |

## Templates

Live under `review_app/templates/account/`. They extend `_base.html`
which is **separate** from the admin `_base.html` so we can iterate on
storefront branding without breaking admin chrome.

CSS lives at `review_app/static/css/account/account.css`. Phase 6 will
polish this.

## Security notes

- Magic links use HS256 JWTs signed with `SECRET_KEY`.
- Tokens are stored as SHA-256 hashes (never plaintext) in
  `customer_login_tokens`.
- Used + expired tokens are detected by both the JWT `exp` claim and the
  DB row's `used_at` / `expires_at` columns — defense in depth.
- Email change re-verifies via a fresh magic link.
