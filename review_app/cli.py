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

from datetime import UTC, datetime
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
        user.deleted_at = datetime.now(UTC)
        session.add(user)

    click.secho(f"deactivated {email_norm}", fg="yellow")


# ---------------------------------------------------------------------------
# Phase 5a — manual cron firing
# ---------------------------------------------------------------------------
@click.command("cron-fire")
@click.argument("job_name")
def cron_fire(job_name: str) -> None:
    """Enqueue a single cron job for IMMEDIATE execution.

    JOB_NAME must be one of the ids in
    :data:`review_app.scheduler.cron.JOB_CATALOG` (e.g. ``drain_outbox``,
    ``refresh_prodigi_quotes``, ``cleanup_render_outputs``,
    ``monitor_failed_callbacks``, ``monitor_dead_outbox``).

    Useful for: dogfooding a job after a deploy without waiting for its
    natural cadence; debugging by running a job synchronously and reading
    the result; admin "fire now" buttons (which shell out to this).
    """
    from review_app.scheduler import enqueue_now
    from review_app.scheduler.cron import JOB_CATALOG

    if job_name not in JOB_CATALOG:
        raise click.UsageError(
            f"Unknown job {job_name!r}. Valid: {sorted(JOB_CATALOG.keys())}"
        )

    func_lookup: dict[str, str] = {
        "drain_outbox": "review_app.queue.jobs:drain_outbox_job",
        "refresh_prodigi_quotes": "review_app.prodigi.quote_refresh:refresh_all_skus_job",
        "cleanup_render_outputs": "review_app.scheduler.jobs:cleanup_old_render_outputs",
        "monitor_failed_callbacks": "review_app.scheduler.jobs:monitor_failed_callbacks",
        "monitor_dead_outbox": "review_app.scheduler.jobs:monitor_dead_outbox",
    }
    target = func_lookup[job_name]
    module_name, _, func_name = target.partition(":")
    import importlib

    mod = importlib.import_module(module_name)
    func = getattr(mod, func_name)
    job = enqueue_now(func)
    click.secho(f"enqueued {job_name} as job_id={job.id}", fg="green")


def register(app: Flask) -> None:
    """Attach all CLI commands to the given Flask app's `cli` group.

    Idempotent: re-registering the same command name overwrites the prior
    binding in Click's group registry.
    """
    app.cli.add_command(create_admin)
    app.cli.add_command(set_role)
    app.cli.add_command(deactivate_user)
    app.cli.add_command(cron_fire)


__all__ = ["create_admin", "cron_fire", "deactivate_user", "register", "set_role"]
