# Admin auth bootstrap & cutover

Phase 0.6 ships admin auth in **shadow mode**: the code is deployed and the
`users` table exists, but `@requires_role` is a passthrough until the
`ADMIN_AUTH_ENABLED` env var is set to `true`. This lets us provision the
first admin account without locking ourselves out.

## One-time bootstrap (after Phase 0.7 pytest harness lands)

Once the pytest + Flask CLI harness is in place, create the first admin from
your local machine pointing at the prod DB (or via `coolify exec` on the
container):

```bash
# Local against prod (preferred — uses your machine's TLS cert store):
DATABASE_URL=postgresql://...prod-url... \
  flask --app review_app.app create-admin grant@benedict.family
# Click prompts for the password twice (>=12 chars enforced).
```

Or inside the running container:

```bash
ssh root@134.122.113.128
docker exec -it <wildprint-container> flask create-admin grant@benedict.family
```

Verify the row landed:

```bash
docker exec o630hdmppejmchbw7gn2qmn2 \
  psql -U postgres -d wildprint -c "SELECT email, role, created_at FROM users;"
```

## Cutover: flipping enforcement on

Once the bootstrap admin exists and you've confirmed login works on the
deployed app:

1. **Coolify dashboard** → wildprint app → **Environment Variables**
2. Add: `ADMIN_AUTH_ENABLED=true`
3. **Redeploy** the app (env-var changes require a restart).
4. Smoke-test: hit `/admin` in an incognito window — should redirect to
   `/admin/login`.

To roll back (panic button): set `ADMIN_AUTH_ENABLED=false` and redeploy.
The decorator becomes a passthrough again; admin routes go open-access. Only
do this if locked out and the bootstrap admin needs re-creation.

## Day-2 user management

```bash
# Add a staff user (read-only console access, no destructive ops):
flask create-admin alice@example.com  # then immediately:
flask set-role alice@example.com staff

# Demote / promote:
flask set-role bob@example.com viewer

# Deactivate (soft-delete; can be reversed by clearing deleted_at directly):
flask deactivate-user bob@example.com
```

Roles in the current model:

| Role     | Intent                                                            |
|----------|-------------------------------------------------------------------|
| `admin`  | Full access including destructive ops (refunds, deletes, secrets) |
| `staff`  | Day-to-day order ops, render queue management, customer support   |
| `viewer` | Read-only dashboards (analytics, AI usage, order list)            |

Route-level role gating is per-decorator: each handler declares which roles
may enter via `@requires_role("admin", "staff", ...)`. There's no central
ACL table — gating lives next to the route it protects, which keeps the
model trivial and grep-able.

## Cookies & session keys

- Flask-Login uses Flask's signed-cookie session under keys `_user_id`,
  `_id`, `_fresh`. These are isolated from the existing `unlocked` /
  `email` keys used by the $49 unlock flow — both can coexist in the same
  cookie.
- Session lifetime is "session" (browser-close clears it). `remember=False`
  in `login_user()`; revisit in Phase 4b if a "remember me" toggle is added.
- CSRF is enforced by Flask-WTF on POST endpoints; the login form embeds a
  `csrf_token()` hidden field. Tests disable CSRF via `WTF_CSRF_ENABLED=false`.

## Failure modes & recovery

- **Locked out (no admin user)**: set `ADMIN_AUTH_ENABLED=false`, redeploy,
  bootstrap an admin, flip back to `true`, redeploy.
- **Forgot password**: there is no self-serve reset in Phase 0.6. Run
  `flask deactivate-user <email>` followed by `flask create-admin <email>`
  with a new password; the partial unique index on `email WHERE deleted_at
  IS NULL` makes this safe.
- **CITEXT not available** (e.g., a fresh ephemeral environment without the
  extension): the migration runs `CREATE EXTENSION IF NOT EXISTS citext`
  first; if your role lacks `CREATE EXTENSION` permission, grant it via a
  superuser or pre-create the extension in the DB.
