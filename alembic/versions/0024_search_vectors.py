"""search_vector tsvector columns + GIN indexes (Postgres-only).

Revision ID: 0024_search_vectors
Revises: 0023_render_presets
Create Date: 2026-05-05

Phase 6 polish: backs the cmd+K cross-entity search with full-text indexes
on Postgres. SQLite test fixtures fall back to LIKE queries (the search
service handles both dialects) so this migration is a NO-OP on SQLite.

Adds tsvector columns + GIN indexes + insert/update triggers on:

* orders            — stripe_payment_intent_id + id::text
* customers         — email + name
* prodigi_skus      — internal_sku + finish + size_inches
* audit_log         — action + target_type + target_id

The tsvector is maintained by triggers so reads stay simple. We use
``simple`` dictionary instead of ``english`` because most of the searchable
content is identifiers (UUIDs, SKU codes, IDs) — stemming would corrupt
matches.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_search_vectors"
down_revision: str | Sequence[str] | None = "0023_render_presets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES_AND_FIELDS: list[tuple[str, list[str]]] = [
    ("orders", ["stripe_payment_intent_id", "id::text"]),
    ("customers", ["email", "name"]),
    ("prodigi_skus", ["internal_sku", "finish", "size_inches"]),
    ("audit_log", ["action", "target_type", "target_id"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (test fixtures) — search service uses LIKE fallbacks.
        return

    for table, fields in _TABLES_AND_FIELDS:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS search_vector tsvector"
        )
        # Backfill UPDATE references the columns directly (table context).
        update_clauses = [f"COALESCE({f}::text, '')" for f in fields]
        update_args = ", ".join(update_clauses)
        update_sql = (
            f"UPDATE {table} SET search_vector = "
            f"to_tsvector('simple', concat_ws(' ', {update_args}))"
        )
        op.execute(update_sql)
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_search_vector "
            f"ON {table} USING GIN (search_vector)"
        )
        # Per-row trigger body MUST qualify columns with NEW. — bare column
        # names don't resolve inside a plpgsql BEFORE INSERT/UPDATE trigger.
        trigger_clauses = [f"COALESCE(NEW.{f}::text, '')" for f in fields]
        trigger_exprs = ", ' ', ".join(trigger_clauses)
        trig_fn = f"{table}_search_vector_update"
        body = (
            f"NEW.search_vector := to_tsvector('simple', "
            f"concat_ws(' ', {trigger_exprs}));\nRETURN NEW;"
        )
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {trig_fn}() RETURNS trigger AS $$
            BEGIN
                {body}
            END
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_{trig_fn} ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER trg_{trig_fn}
            BEFORE INSERT OR UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {trig_fn}()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, _ in _TABLES_AND_FIELDS:
        trig_fn = f"{table}_search_vector_update"
        op.execute(f"DROP TRIGGER IF EXISTS trg_{trig_fn} ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {trig_fn}()")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_search_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS search_vector")
