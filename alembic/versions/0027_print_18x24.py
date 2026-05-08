"""Update tier 3 print master from 24x36 (7200x10800, 2:3) to 18x24 (5400x7200, 3:4) to match Prodigi 18x24 frame SKUs.

Revision ID: 0027_print_18x24
Revises: 0026_fix_audit_trigger
Create Date: 2026-05-07

Tier 3 is migrating from a 24"x36" print master (7200x10800 @ 300 DPI, 2:3
aspect) to an 18"x24" master (5400x7200 @ 300 DPI, 3:4 aspect) to match the
Prodigi 18x24 framed-print SKUs we actually fulfill against. The hardcoded
``review_app.render.tiers.TIER_CONFIG`` baseline already reflects the new
dimensions; this migration brings the DB-backed override row in
``render_presets`` (seeded by 0023) into agreement so admins editing the
live config don't see stale numbers.

Tier 1 and tier 2 are unchanged.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_print_18x24"
down_revision: str | Sequence[str] | None = "0026_fix_audit_trigger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE render_presets SET long_edge_px = 7200 WHERE tier = 3")


def downgrade() -> None:
    op.execute("UPDATE render_presets SET long_edge_px = 10800 WHERE tier = 3")
