"""User model — admin / staff with argon2id password hashing.

Schema mirrors the design in docs/db-schema.md §1. The CITEXT email column
provides case-insensitive uniqueness on Postgres; on SQLite (test env) the
column degrades to TEXT and we lower()-normalize at the model layer to
preserve the same semantics.

Argon2id is used per OWASP recommendation. The hash string embeds the params
(memory, time, parallelism, salt), so future param tuning needs only a
re-hash on next successful login — no schema change.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from review_app.db.base import Base, TimestampMixin, UUIDPKMixin, uuid7

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Module-level hasher: argon2-cffi recommends a single instance across the
# process. Defaults are the OWASP 2023 baseline (m=64MiB, t=3, p=4) — adjust
# only with benchmark evidence on the target hardware.
_PH: PasswordHasher = PasswordHasher()


VALID_ROLES: frozenset[str] = frozenset({"admin", "staff", "viewer"})


class User(Base, UUIDPKMixin, TimestampMixin, UserMixin):  # type: ignore[misc]  # flask-login UserMixin has type Any
    """Admin / staff user account."""

    __tablename__ = "users"

    # Email: stored case-insensitively. On Postgres this uses CITEXT (set in
    # the migration); the Python type is plain str either way. We always
    # store the lower()'d form so that SQLite and Postgres behave identically.
    email: Mapped[str] = mapped_column(Text, nullable=False)

    # Argon2id-hashed password. Hash string includes params + salt.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # One of: 'admin', 'staff', 'viewer'. Enforced by CHECK constraint.
    role: Mapped[str] = mapped_column(Text, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Soft-delete marker. Active rows have deleted_at IS NULL.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'staff', 'viewer')",
            name="role_in_enum",
        ),
        Index(
            "users_email_active_uq",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------
    @classmethod
    def hash_password(cls, plain: str) -> str:
        """Hash a plaintext password with argon2id. Returns the encoded hash."""
        if not plain:
            raise ValueError("Password must not be empty.")
        return _PH.hash(plain)

    def verify_password(self, plain: str) -> bool:
        """Constant-time-ish argon2 verify. Returns False on mismatch."""
        if not self.password_hash:
            return False
        try:
            _PH.verify(self.password_hash, plain)
        except VerifyMismatchError:
            return False
        except Exception:
            # Corrupt hash, unsupported algo, etc. Fail closed.
            return False
        # Opportunistic re-hash if argon2 params have been bumped since this
        # hash was generated. Caller is responsible for committing.
        if _PH.check_needs_rehash(self.password_hash):
            self.password_hash = _PH.hash(plain)
        return True

    # ------------------------------------------------------------------
    # Flask-Login integration
    # ------------------------------------------------------------------
    def get_id(self) -> str:
        """Flask-Login expects a string. UUID -> str."""
        return str(self.id)

    @property
    def is_active(self) -> bool:
        """Flask-Login: inactive users are rejected at login_user()."""
        return self.deleted_at is None

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    @classmethod
    def get_active_by_id(cls, session: Session, user_id: str | uuid.UUID) -> User | None:
        """Look up a non-deleted user by primary key. Returns None if absent."""
        try:
            uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        except (ValueError, AttributeError):
            return None
        # Fetch by PK then filter — get() bypasses the partial-unique-index issue.
        # Note: SQLite stores UUID as String(36); Postgres as native UUID. Both
        # accept the UUID instance via SQLAlchemy's type adaptation.
        user = session.get(cls, uid)
        if user is None or user.deleted_at is not None:
            return None
        return user

    @classmethod
    def get_active_by_email(cls, session: Session, email: str) -> User | None:
        """Case-insensitive lookup of an active user by email."""
        from sqlalchemy import func, select

        normalized = email.strip().lower()
        # On Postgres CITEXT makes this redundant but harmless; on SQLite this
        # is what enforces case-insensitive matching.
        stmt = select(cls).where(
            func.lower(cls.email) == normalized,
            cls.deleted_at.is_(None),
        )
        return session.execute(stmt).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def create(cls, *, email: str, password: str, role: str) -> User:
        """Build a new User instance with hashed password.

        Caller is responsible for adding to a session and committing.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role {role!r}. Expected one of {sorted(VALID_ROLES)}.")
        return cls(
            id=uuid7(),
            email=email.strip().lower(),
            password_hash=cls.hash_password(password),
            role=role,
        )

    def __repr__(self) -> str:
        return f"<User {self.email!r} role={self.role!r}>"


__all__ = ["VALID_ROLES", "User"]
