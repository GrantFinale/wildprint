"""users table — admin / staff with role-based access control.

Revision ID: 0002_users
Revises: 0001_baseline
Create Date: 2026-05-05

Creates the `users` table per docs/db-schema.md:
- UUIDv7 PK
- CITEXT email (case-insensitive matching without lower()-everywhere)
- argon2id password hash (params embedded in the hash string)
- role TEXT with CHECK constraint (admin|staff|viewer)
- soft-delete via `deleted_at`
- partial unique index on email WHERE deleted_at IS NULL

The CITEXT extension is created idempotently. On SQLite (used for unit tests)
CITEXT is not available, so the migration falls back to plain TEXT — model
verification still uses lower()'d email comparisons there.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_users"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS citext")
        email_type: sa.types.TypeEngine[str] = sa.dialects.postgresql.CITEXT()
        uuid_type: sa.types.TypeEngine = sa.dialects.postgresql.UUID(as_uuid=True)
    else:
        # SQLite (tests). CITEXT and UUID not native — fall back gracefully.
        email_type = sa.Text()
        uuid_type = sa.String(length=36)

    op.create_table(
        "users",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("email", email_type, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "role IN ('admin', 'staff', 'viewer')",
            name="role_in_enum",
        ),
    )

    # Partial unique index: only enforce uniqueness on active (non-deleted) rows.
    # Postgres supports partial indexes natively. SQLite supports them too
    # (3.8+) so this works in both environments.
    op.create_index(
        "users_email_active_uq",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("users_email_active_uq", table_name="users")
    op.drop_table("users")
    if _is_postgres():
        # Drop the extension only if no other table depends on it. Safe in
        # this migration ordering: 0002 is the first CITEXT consumer.
        op.execute("DROP EXTENSION IF EXISTS citext")
