"""notes — admin notes attached to orders or customers.

Revision ID: 0019_notes
Revises: 0018_content_blocks
Create Date: 2026-05-05

Phase 5b: lightweight notes table. Polymorphic via ``(target_type, target_id)``
since we don't need cross-cutting joins in any query path. Soft-deleted via
``deleted_at`` so admins can audit-recover an accidentally removed note.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_notes"
down_revision: str | Sequence[str] | None = "0018_content_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", _uuid_type(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", _uuid_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_notes_author_user_id_users",
        ),
        sa.CheckConstraint(
            "target_type IN ('order','customer')",
            name="ck_notes_target_type",
        ),
    )
    op.create_index(
        "ix_notes_target_type_target_id_created_at",
        "notes",
        ["target_type", "target_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notes_target_type_target_id_created_at", table_name="notes"
    )
    op.drop_table("notes")
