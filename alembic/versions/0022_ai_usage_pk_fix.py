"""ai_usage_log.id — dialect-portable PK pattern (NO-OP on Postgres).

Revision ID: 0022_ai_usage_pk_fix
Revises: 0021_reprint_cost
Create Date: 2026-05-05

Phase 6 polish: ``review_app.ai.models.AIUsageLog`` previously declared its
PK as a raw :class:`sqlalchemy.BigInteger`, which works fine on Postgres
(BIGSERIAL) but does NOT autoincrement on SQLite — SQLite only treats
``INTEGER PRIMARY KEY`` (not ``BIGINT PRIMARY KEY``) as a rowid alias.

The fix is a model-level declaration change to use the same
``BigInteger().with_variant(Integer(), "sqlite")`` pattern that
``review_app.email.outbox`` uses. That gives Postgres a real BIGINT and
SQLite a plain INTEGER PK that autoincrements naturally — so tests no
longer need to pass explicit ``id=N`` workarounds.

This migration is intentionally a NO-OP:

* Postgres already has the column as ``BIGINT`` (BIGSERIAL via the
  0003 baseline migration), and altering the type would require
  rewriting every row of a high-volume telemetry table for zero gain.
* SQLite databases are ephemeral test fixtures — they're recreated from
  scratch via ``Base.metadata.create_all``, which now picks up the new
  ``with_variant`` declaration automatically. No ALTER needed.

Migration exists for chain continuity (so 0023+ have a parent revision)
and to record the fix in the schema history.
"""
from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0022_ai_usage_pk_fix"
down_revision: str | Sequence[str] | None = "0021_reprint_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Intentional no-op. See module docstring.
    pass


def downgrade() -> None:
    # Intentional no-op — there is nothing to revert.
    pass
