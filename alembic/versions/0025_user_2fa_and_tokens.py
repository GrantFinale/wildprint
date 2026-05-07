"""users.2fa columns + new user_api_tokens table.

Revision ID: 0025_user_2fa_and_tokens
Revises: 0024_search_vectors
Create Date: 2026-05-05

Phase 6 polish: adds TOTP secret + recovery codes to ``users`` and a
new ``user_api_tokens`` table for per-user programmatic API access.

Tokens are stored as SHA-256 hashes (column ``token_hash``); the
plaintext is shown to the user exactly once at creation time and never
persisted. ``scopes`` is a JSON-encoded array of capability strings.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_user_2fa_and_tokens"
down_revision: str | Sequence[str] | None = "0024_search_vectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 2FA columns on the users table.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("totp_secret", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "totp_enrolled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        # Recovery codes — JSON list of SHA-256 hashes. We use Text + JSON
        # at the model layer so SQLite tests stay simple; Postgres can
        # later be migrated to JSONB without a code change.
        batch.add_column(
            sa.Column(
                "totp_recovery_codes_hashed",
                sa.Text(),
                nullable=True,
            )
        )

    # New per-user API tokens table.
    op.create_table(
        "user_api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        # JSON list of scope strings — same Text-based portability strategy.
        sa.Column("scopes", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
    )
    op.create_index(
        "ix_user_api_tokens_user_id",
        "user_api_tokens",
        ["user_id"],
    )
    # Partial index for active (non-revoked) lookups on Postgres.
    if is_pg:
        op.create_index(
            "ix_user_api_tokens_active_hash",
            "user_api_tokens",
            ["token_hash"],
            postgresql_where=sa.text("revoked_at IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        op.drop_index(
            "ix_user_api_tokens_active_hash",
            table_name="user_api_tokens",
        )
    op.drop_index("ix_user_api_tokens_user_id", table_name="user_api_tokens")
    op.drop_table("user_api_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("totp_recovery_codes_hashed")
        batch.drop_column("totp_enrolled_at")
        batch.drop_column("totp_secret")
