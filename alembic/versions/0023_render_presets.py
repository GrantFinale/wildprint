"""render_presets — DB-backed editable tier configuration.

Revision ID: 0023_render_presets
Revises: 0022_ai_usage_pk_fix
Create Date: 2026-05-05

Phase 6 polish: replaces the hardcoded ``review_app.render.tiers.TIER_CONFIG``
dict with a DB-backed table so admins can tune the three render tiers
(thumbnail / preview / print) from ``/admin/catalog/render-presets``
without a code deploy.

The hardcoded dict is kept as a fallback when the DB row is absent
(e.g. migration order, fresh dev sqlite without a seed yet) — see
``review_app.render.tiers.get_tier_config``.

Seed inserts mirror the locked Phase 2 spec table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_render_presets"
down_revision: str | Sequence[str] | None = "0022_ai_usage_pk_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "render_presets",
        sa.Column("tier", sa.SmallInteger(), primary_key=True, nullable=False),
        sa.Column("long_edge_px", sa.Integer(), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("jpeg_quality", sa.Integer(), nullable=True),
        sa.Column(
            "watermark_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("watermark_text", sa.Text(), nullable=True),
        sa.Column(
            "watermark_opacity",
            sa.Numeric(precision=4, scale=3),
            nullable=True,
        ),
        sa.Column(
            "watermark_angle",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("bucket_env_var", sa.Text(), nullable=False),
        sa.Column(
            "public_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("tier IN (1, 2, 3)", name="render_presets_tier_in_enum"),
        sa.CheckConstraint(
            "format IN ('jpeg', 'png', 'webp')",
            name="render_presets_format_in_enum",
        ),
        sa.CheckConstraint(
            "long_edge_px BETWEEN 100 AND 12000",
            name="render_presets_long_edge_range",
        ),
        sa.CheckConstraint(
            "dpi BETWEEN 72 AND 600",
            name="render_presets_dpi_range",
        ),
        sa.CheckConstraint(
            "jpeg_quality IS NULL OR jpeg_quality BETWEEN 50 AND 95",
            name="render_presets_quality_range",
        ),
    )

    # Seed the three tiers from the Phase 2 spec. Mirrors TIER_CONFIG.
    op.execute(
        """
        INSERT INTO render_presets
            (tier, long_edge_px, dpi, format, jpeg_quality,
             watermark_enabled, watermark_text, watermark_opacity, watermark_angle,
             bucket_env_var, public_read)
        VALUES
            (1,  400, 72, 'jpeg', 85, false, NULL, NULL, NULL,
             'SPACES_THUMBS_BUCKET',   true),
            (2, 2400, 72, 'jpeg', 85, true,  'fishingposter.com', 0.15, 30,
             'SPACES_PREVIEWS_BUCKET', true),
            (3, 10800, 300, 'png', NULL, false, NULL, NULL, NULL,
             'SPACES_POSTERS_BUCKET',  false)
        """
    )


def downgrade() -> None:
    op.drop_table("render_presets")
