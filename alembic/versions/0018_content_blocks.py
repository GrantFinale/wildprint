"""content_blocks — DB-backed content store (replaces in-memory dicts).

Revision ID: 0018_content_blocks
Revises: 0017_reprints
Create Date: 2026-05-05

Phase 5b: replaces the Phase 4b in-memory ``_email_templates`` and
``_marketing_content`` dicts in ``review_app.admin.content.routes`` with a
persistent table. Single key namespace handles both:

* ``homepage_hero``, ``about_us``, ``faq`` — marketing slots (slot='marketing').
* ``email.<kind>.subject`` / ``email.<kind>.html`` — email template kinds
  (slot='email'). Convention: split on the second dot.

``body`` stores either Markdown or HTML; the application chooses based on
``slot`` (marketing -> Markdown rendered via the ``markdown`` package;
email -> raw HTML).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_content_blocks"
down_revision: str | Sequence[str] | None = "0017_reprints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "content_blocks",
        sa.Column("key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_by_user_id", _uuid_type(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_content_blocks_updated_by_user_id_users",
        ),
    )
    op.create_index("ix_content_blocks_slot", "content_blocks", ["slot"])


def downgrade() -> None:
    op.drop_index("ix_content_blocks_slot", table_name="content_blocks")
    op.drop_table("content_blocks")
