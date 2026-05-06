"""baseline — empty migration that establishes alembic_version.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-05

This migration intentionally contains no schema changes. It exists so that
Alembic has a head revision to anchor against; subsequent sub-tasks (auth,
ai-usage, orders, etc.) will add tables in their own revisions chained off
this one.
"""
from __future__ import annotations

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op baseline."""


def downgrade() -> None:
    """No-op baseline."""
