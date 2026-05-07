"""Fix search_vector trigger functions to qualify columns with NEW.

Revision ID: 0026_fix_audit_trigger
Revises: 0025_user_2fa_and_tokens
Create Date: 2026-05-07

0024 created BEFORE INSERT/UPDATE trigger functions whose bodies referenced
bare column names (``COALESCE(action::text, '')``) instead of NEW-qualified
references (``COALESCE(NEW.action::text, '')``). Inside a plpgsql trigger
those bare names don't resolve to the row being mutated, so every INSERT
on the affected tables raised ``UndefinedColumn``.

Most visibly this killed the audit_log: every admin POST/PATCH/DELETE
triggered ``audit.after_request failed: column "action" does not exist``
and the audit row was never written.

This migration regenerates the four trigger functions with NEW-qualified
column refs. 0024 has also been corrected so a fresh deploy installs the
right SQL from the start; this migration exists for environments where
0024 already ran with the broken body.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_fix_audit_trigger"
down_revision: str | Sequence[str] | None = "0025_user_2fa_and_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirror 0024's table list so we regenerate every affected trigger.
_TABLES_AND_FIELDS: list[tuple[str, list[str]]] = [
    ("orders", ["stripe_payment_intent_id", "id::text"]),
    ("customers", ["email", "name"]),
    ("prodigi_skus", ["internal_sku", "finish", "size_inches"]),
    ("audit_log", ["action", "target_type", "target_id"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, fields in _TABLES_AND_FIELDS:
        # Per-row trigger body MUST qualify columns with NEW.
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
        # Trigger itself doesn't need re-binding — it points at the function
        # by name, and CREATE OR REPLACE FUNCTION rewrites the body in place.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Restore the pre-fix bodies so downgrades don't pretend the bug was
    # never there. The bare-column form will fail at INSERT time, which
    # matches the state 0024 left the DB in.
    for table, fields in _TABLES_AND_FIELDS:
        bare_clauses = [f"COALESCE({f}::text, '')" for f in fields]
        bare_exprs = ", ' ', ".join(bare_clauses)
        trig_fn = f"{table}_search_vector_update"
        body = (
            f"NEW.search_vector := to_tsvector('simple', "
            f"concat_ws(' ', {bare_exprs}));\nRETURN NEW;"
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
