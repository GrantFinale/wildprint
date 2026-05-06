"""Click commands for admin user management.

Registered into Flask via `cli.register(app)` from the wiring pass:

    from review_app import cli
    cli.register(app)

Commands:
    flask create-admin <email>      — bootstrap a new admin user
    flask set-role <email> <role>   — change a user's role
    flask deactivate-user <email>   — soft-delete a user (sets deleted_at)

These are intentionally NOT auto-registered at import time so this module
stays a pure no-op until the wiring pass calls `register()`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import click

from review_app.auth.models import VALID_ROLES, User
from review_app.db import get_session

if TYPE_CHECKING:
    from flask import Flask


@click.command("create-admin")
@click.argument("email")
@click.password_option(
    "--password",
    prompt=True,
    confirmation_prompt=True,
    hide_input=True,
    help="Admin password (prompted twice if not provided).",
)
def create_admin(email: str, password: str) -> None:
    """Create a new user with role=admin.

    Example::

        flask create-admin grant@benedict.family
    """
    email_norm = email.strip().lower()
    if not email_norm:
        raise click.UsageError("Email must not be empty.")
    if len(password) < 12:
        raise click.UsageError("Password must be at least 12 characters.")

    with get_session() as session:
        existing = User.get_active_by_email(session, email_norm)
        if existing is not None:
            raise click.ClickException(
                f"User {email_norm!r} already exists (role={existing.role}). "
                f"Use `flask set-role` to change the role, or `flask deactivate-user` first."
            )
        user = User.create(email=email_norm, password=password, role="admin")
        session.add(user)
        # get_session() commits on clean exit.
    click.secho(f"created admin user {email_norm}", fg="green")


@click.command("set-role")
@click.argument("email")
@click.argument("role")
def set_role(email: str, role: str) -> None:
    """Update a user's role. ROLE must be one of: admin, staff, viewer."""
    email_norm = email.strip().lower()
    role_norm = role.strip().lower()
    if role_norm not in VALID_ROLES:
        raise click.UsageError(
            f"Invalid role {role!r}. Expected one of {sorted(VALID_ROLES)}."
        )

    with get_session() as session:
        user = User.get_active_by_email(session, email_norm)
        if user is None:
            raise click.ClickException(f"No active user with email {email_norm!r}.")
        old_role = user.role
        user.role = role_norm
        session.add(user)

    click.secho(f"updated {email_norm}: {old_role} -> {role_norm}", fg="green")


@click.command("deactivate-user")
@click.argument("email")
@click.confirmation_option(
    prompt="Soft-delete this user? They will be unable to log in.",
)
def deactivate_user(email: str) -> None:
    """Soft-delete a user (sets deleted_at). Reversible by clearing the column."""
    email_norm = email.strip().lower()
    with get_session() as session:
        user = User.get_active_by_email(session, email_norm)
        if user is None:
            raise click.ClickException(f"No active user with email {email_norm!r}.")
        user.deleted_at = datetime.now(timezone.utc)
        session.add(user)

    click.secho(f"deactivated {email_norm}", fg="yellow")


def register(app: "Flask") -> None:
    """Attach all CLI commands to the given Flask app's `cli` group.

    Idempotent: re-registering the same command name overwrites the prior
    binding in Click's group registry.
    """
    app.cli.add_command(create_admin)
    app.cli.add_command(set_role)
    app.cli.add_command(deactivate_user)


__all__ = ["create_admin", "deactivate_user", "register", "set_role"]
