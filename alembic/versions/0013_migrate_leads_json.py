"""Data migration: copy ``metadata/leads.json`` into ``customers`` (+ orders).

Revision ID: 0013_migrate_leads_json
Revises: 0012_refunds
Create Date: 2026-05-05

Phase 3a:

For each lead in ``metadata/leads.json`` we:

1. Insert a ``customers`` row with ``email``, ``legacy_lake_name=lake_name``,
   ``legacy_state=state``, ``marketing_opt_in=False``,
   ``created_by_migration='0013_migrate_leads_json'``.
2. If ``paid: true``: insert a synthetic ``orders`` row representing the
   $49 digital unlock — ``status='paid'``, ``source='legacy_$49_unlock'``,
   ``total_cents=4900``, no shipping address, no order_items (digital),
   ``stripe_payment_intent_id`` = ``'legacy_' + stripe_session_id``.

**Idempotent:** re-runs skip customers that already exist by email and
orders that already exist by ``stripe_payment_intent_id``.

**Crash-safe:** if ``metadata/leads.json`` doesn't exist (e.g. fresh CI
checkout without prod data), the migration logs and returns cleanly.

**Reversible:** ``downgrade()`` deletes only rows tagged
``created_by_migration='0013_migrate_leads_json'`` (customers) and
``source='legacy_$49_unlock'`` (orders) — synthetic rows minted here.
Anything created by app code stays put.

The original ``leads.json`` is NOT deleted by this migration — coexistence
with the existing $49 unlock flow is by design (see
``docs/phase-3-schema.md``).
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_migrate_leads_json"
down_revision: str | Sequence[str] | None = "0012_refunds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration.0013_migrate_leads_json")

# Sentinel values used by both upgrade() and downgrade() to scope row deletes.
MIGRATION_TAG: str = "0013_migrate_leads_json"
LEGACY_ORDER_SOURCE: str = "legacy_$49_unlock"
LEGACY_PI_PREFIX: str = "legacy_"
LEGACY_UNLOCK_PRICE_CENTS: int = 4900


def _leads_path() -> Path:
    """Resolve ``<repo_root>/metadata/leads.json`` from this migration file's
    location. Works regardless of the alembic invocation cwd."""
    here = Path(__file__).resolve()
    # alembic/versions/0013_migrate_leads_json.py → repo_root/metadata/leads.json
    repo_root = here.parent.parent.parent
    return repo_root / "metadata" / "leads.json"


def _load_leads_safely(path: Path) -> list[dict[str, Any]]:
    """Read leads.json. Returns [] if the file is missing or malformed.

    A missing file is the expected case in CI / fresh checkouts. A malformed
    file in a production migration would be a critical failure, but we still
    avoid raising here — log loudly and return [] so the schema migration
    chain keeps moving. Operators can re-run after fixing the file.
    """
    if not path.exists():
        log.info("leads.json not found at %s — skipping data migration.", path)
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        log.warning("leads.json at %s is not a JSON array — skipping.", path)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to read leads.json at %s: %s — skipping.", path, exc)
    return []


def _parse_iso_dt(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse. Returns None on failure."""
    if not value or not isinstance(value, str):
        return None
    # datetime.fromisoformat handles 'YYYY-MM-DDTHH:MM:SS+00:00' and the
    # 'Z' suffix isn't supported pre-3.11 — leads.py uses isoformat() so
    # the offset is always explicit. Strip any trailing Z just in case.
    cleaned = value.rstrip("Z")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _normalize_uuid_for_dialect(
    value: uuid.UUID, dialect_name: str
) -> uuid.UUID | str:
    """SQLite stores UUID as a 32-char hex string; Postgres uses native UUID.

    SQLAlchemy core's ``insert(...).values(...)`` round-trips ``uuid.UUID``
    on both, but ``sa.Table.insert()`` against a reflected SQLite table
    (where the column is TEXT) wants a string. Be explicit so neither
    backend surprises us.
    """
    if dialect_name == "sqlite":
        return value.hex
    return value


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    leads = _load_leads_safely(_leads_path())
    if not leads:
        log.info("No leads to migrate — upgrade is a no-op.")
        return

    customers = sa.Table("customers", sa.MetaData(), autoload_with=bind)
    orders = sa.Table("orders", sa.MetaData(), autoload_with=bind)

    inserted_customers = 0
    inserted_orders = 0
    skipped_customers = 0
    skipped_orders = 0

    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if not email or "@" not in email:
            log.warning("Skipping lead with invalid email: %r", lead)
            continue

        # ---- Idempotent customer insert ----
        existing_customer_id_row = bind.execute(
            sa.select(customers.c.id).where(
                sa.func.lower(customers.c.email) == email,
                customers.c.deleted_at.is_(None),
            )
        ).first()

        if existing_customer_id_row is not None:
            customer_id_raw = existing_customer_id_row[0]
            customer_id = (
                customer_id_raw
                if isinstance(customer_id_raw, uuid.UUID)
                else uuid.UUID(str(customer_id_raw))
            )
            skipped_customers += 1
        else:
            customer_id = uuid.uuid4()
            created_at_dt = _parse_iso_dt(lead.get("created_at")) or datetime.now(UTC)
            updated_at_dt = (
                _parse_iso_dt(lead.get("last_seen_at")) or created_at_dt
            )
            bind.execute(
                customers.insert().values(
                    id=_normalize_uuid_for_dialect(customer_id, dialect_name),
                    email=email,
                    name=None,
                    stripe_customer_id=None,
                    marketing_opt_in=False,
                    legacy_lake_name=(lead.get("lake_name") or None) or None,
                    legacy_state=(lead.get("state") or None) or None,
                    created_by_migration=MIGRATION_TAG,
                    last_login_at=None,
                    deleted_at=None,
                    created_at=created_at_dt,
                    updated_at=updated_at_dt,
                )
            )
            inserted_customers += 1

        # ---- Idempotent synthetic order insert (only for paid leads) ----
        if not lead.get("paid"):
            continue
        stripe_session_id = (lead.get("stripe_session_id") or "").strip()
        if not stripe_session_id:
            log.warning(
                "Lead %s is paid but has no stripe_session_id — skipping order.",
                email,
            )
            continue

        legacy_pi = f"{LEGACY_PI_PREFIX}{stripe_session_id}"
        existing_order = bind.execute(
            sa.select(orders.c.id).where(
                orders.c.stripe_payment_intent_id == legacy_pi
            )
        ).first()
        if existing_order is not None:
            skipped_orders += 1
            continue

        unlocked_at_dt = _parse_iso_dt(lead.get("unlocked_at"))
        order_id = uuid.uuid4()
        order_created_at = unlocked_at_dt or datetime.now(UTC)
        bind.execute(
            orders.insert().values(
                id=_normalize_uuid_for_dialect(order_id, dialect_name),
                customer_id=_normalize_uuid_for_dialect(
                    customer_id, dialect_name
                ),
                shipping_address_id=None,
                stripe_payment_intent_id=legacy_pi,
                status="paid",
                subtotal_cents=LEGACY_UNLOCK_PRICE_CENTS,
                shipping_cents=0,
                tax_cents=0,
                total_cents=LEGACY_UNLOCK_PRICE_CENTS,
                currency="USD",
                placed_at=order_created_at,
                paid_at=unlocked_at_dt or order_created_at,
                shipped_at=None,
                delivered_at=None,
                source=LEGACY_ORDER_SOURCE,
                created_at=order_created_at,
                updated_at=order_created_at,
            )
        )
        inserted_orders += 1

    log.info(
        "leads.json migration complete: "
        "inserted_customers=%d skipped_customers=%d "
        "inserted_orders=%d skipped_orders=%d",
        inserted_customers,
        skipped_customers,
        inserted_orders,
        skipped_orders,
    )


def downgrade() -> None:
    """Delete only rows minted by this migration.

    Order matters: orders → customers (FK from orders.customer_id RESTRICTs
    deletion of a customer that still has orders).
    """
    bind = op.get_bind()
    customers = sa.Table("customers", sa.MetaData(), autoload_with=bind)
    orders = sa.Table("orders", sa.MetaData(), autoload_with=bind)

    deleted_orders = bind.execute(
        orders.delete().where(orders.c.source == LEGACY_ORDER_SOURCE)
    ).rowcount or 0
    deleted_customers = bind.execute(
        customers.delete().where(
            customers.c.created_by_migration == MIGRATION_TAG
        )
    ).rowcount or 0

    log.info(
        "leads.json migration downgrade: "
        "deleted_orders=%d deleted_customers=%d",
        deleted_orders,
        deleted_customers,
    )
