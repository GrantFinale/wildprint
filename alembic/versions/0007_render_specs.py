"""render_specs + render_outputs — content-addressable poster cache.

Revision ID: 0007_render_specs
Revises: 0006_seed_prodigi_skus
Create Date: 2026-05-05

Phase 2: backs the three-tier render system.

* ``render_specs`` is the canonical recipe for one poster (lake, species,
  art style, layout config, renderer_version). The ``spec_hash`` is a
  SHA-256 of the canonicalized inputs and lets us cache by content address.

* ``render_outputs`` holds one row per (spec, tier). UNIQUE(spec_id, tier)
  is the cache lookup key — a hit returns the storage_key directly.

See ``docs/db-schema.md`` and ``docs/render-tiers.md`` for the rationale.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0007_render_specs"
down_revision: str | Sequence[str] | None = "0006_seed_prodigi_skus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_type() -> Any:
    """Cross-dialect UUID column.

    ``sqlalchemy.Uuid`` resolves to native ``UUID`` on Postgres and 32-char
    hex string on SQLite. Round-trips :class:`uuid.UUID` objects directly.
    """
    return sa.Uuid(as_uuid=True)


def _json_type() -> Any:
    """Cross-dialect JSON — JSONB on Postgres, JSON on SQLite."""
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # render_specs
    # ------------------------------------------------------------------
    op.create_table(
        "render_specs",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("spec_hash", sa.Text(), nullable=False),
        sa.Column("canonical_inputs", _json_type(), nullable=False),
        sa.Column("renderer_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("spec_hash", name="uq_render_specs_spec_hash"),
    )
    op.create_index(
        "ix_render_specs_spec_hash",
        "render_specs",
        ["spec_hash"],
    )

    # ------------------------------------------------------------------
    # render_outputs
    # ------------------------------------------------------------------
    op.create_table(
        "render_outputs",
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column(
            "render_spec_id",
            _uuid_type(),
            sa.ForeignKey("render_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("storage_bucket", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("queue_job_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "tier IN (1, 2, 3)",
            name="ck_render_outputs_tier",
        ),
        sa.UniqueConstraint(
            "render_spec_id", "tier", name="uq_render_outputs_spec_tier"
        ),
    )
    op.create_index(
        "ix_render_outputs_render_spec_id",
        "render_outputs",
        ["render_spec_id"],
    )
    op.create_index(
        "ix_render_outputs_generated_at",
        "render_outputs",
        ["generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_render_outputs_generated_at", table_name="render_outputs")
    op.drop_index("ix_render_outputs_render_spec_id", table_name="render_outputs")
    op.drop_table("render_outputs")

    op.drop_index("ix_render_specs_spec_hash", table_name="render_specs")
    op.drop_table("render_specs")
