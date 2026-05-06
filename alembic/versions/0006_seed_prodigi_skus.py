"""Seed the 32 launch SKUs (Classic Frame, 4 sizes × 8 finishes).

Revision ID: 0006_seed_prodigi_skus
Revises: 0005_prodigi_orders
Create Date: 2026-05-05

Seeds ``prodigi_skus`` with the launch catalog per docs/integration-plan.md
decision #4. Sizes are portrait-only at launch (landscape may be added later).

Sizes  : 12x16, 16x20, 18x24, 24x36
Finishes (Prodigi-verbatim names): Black, White, Natural, Antique Silver,
                                   Antique Gold, Brown, Dark Grey, Light Grey

internal_sku format : ``cf-{size}-{finish-slug}`` e.g. ``cf-12x16-black``,
                      ``cf-24x36-light-grey``  (slug = hyphenated for our IDs)
prodigi_sku format  : ``GLOBAL-CFPM-{SIZE}`` e.g. ``GLOBAL-CFPM-12X16``
attributes          : ``{"color": "<prodigi-color>"}`` where ``prodigi-color``
                      is lowercased + space-separated as returned by
                      GET /v4.0/products/GLOBAL-CFPM-{SIZE}'s ``attributes``
                      map. Mapping: Black→black, White→white, Natural→natural,
                      Antique Silver→silver, Antique Gold→gold, Brown→brown,
                      Dark Grey→dark grey, Light Grey→light grey.

Pricing columns (``retail_price_cents``, ``last_quoted_wholesale_cents``,
``margin_cents``) are left NULL — the nightly quote refresh job
(:func:`review_app.prodigi.quote_refresh.refresh_all_skus_job`) populates
the wholesale + last_refreshed_at columns by calling /v4.0/Quotes per SKU.
Retail prices are owned by Phase 4 admin tooling.

Verification: a sandbox call to ``GET /v4.0/products/GLOBAL-CFPM-{SIZE}``
confirmed all four sizes exist and accept ``color`` attribute values
matching our 8 finish slugs. If a SKU returns 404 in production, the
seeded row is harmless (active=true but never matches a real product) —
admin tooling will surface it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_seed_prodigi_skus"
down_revision: Union[str, Sequence[str], None] = "0005_prodigi_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Seed data — keep both lists ordered identically with their slugs.
# ---------------------------------------------------------------------------
SIZES_INCHES: list[str] = ["12x16", "16x20", "18x24", "24x36"]

# (Prodigi-verbatim display name, attribute value sent to /v4.0/Quotes /Orders)
#
# IMPORTANT: Prodigi's `color` attribute uses *space-separated* lowercase
# strings, not hyphenated. The `Antique Silver` and `Antique Gold` finishes
# in our marketing copy correspond to Prodigi's plain `silver` and `gold`
# values — verified via GET /v4.0/products/GLOBAL-CFPM-16X20 returning
# `color: ['black', 'brown', 'dark grey', 'gold', 'light grey', 'natural',
# 'silver', 'white']`.
FINISHES: list[tuple[str, str]] = [
    ("Black", "black"),
    ("White", "white"),
    ("Natural", "natural"),
    ("Antique Silver", "silver"),
    ("Antique Gold", "gold"),
    ("Brown", "brown"),
    ("Dark Grey", "dark grey"),
    ("Light Grey", "light grey"),
]


def _build_seed_rows() -> list[dict[str, str | bool]]:
    rows: list[dict[str, str | bool]] = []
    for size in SIZES_INCHES:
        prodigi_sku = f"GLOBAL-CFPM-{size.upper()}"
        for finish_name, finish_slug in FINISHES:
            internal_sku = f"cf-{size}-{finish_slug}"
            rows.append(
                {
                    "internal_sku": internal_sku,
                    "prodigi_sku": prodigi_sku,
                    "finish": finish_name,
                    "size_inches": size,
                    "orientation": "portrait",
                    "active": True,
                    "in_stock": True,
                }
            )
    return rows


def upgrade() -> None:
    rows = _build_seed_rows()
    if not rows:
        return

    # Reflect the table to get the right column types per dialect, then
    # bulk_insert. Server defaults handle created_at / updated_at.
    bind = op.get_bind()
    metadata = sa.MetaData()
    skus = sa.Table("prodigi_skus", metadata, autoload_with=bind)
    op.bulk_insert(skus, rows)


def downgrade() -> None:
    rows = _build_seed_rows()
    internal_skus = [str(r["internal_sku"]) for r in rows]
    if not internal_skus:
        return
    bind = op.get_bind()
    metadata = sa.MetaData()
    skus = sa.Table("prodigi_skus", metadata, autoload_with=bind)
    op.execute(skus.delete().where(skus.c.internal_sku.in_(internal_skus)))
