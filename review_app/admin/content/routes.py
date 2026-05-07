"""Admin Content routes — email templates, email log, marketing pages.

Endpoints registered on ``admin_bp``:

* ``admin.content_email_templates`` — ``GET/POST /admin/content/email-templates``
* ``admin.content_email_log``       — ``GET/POST /admin/content/email-log``
* ``admin.content_marketing``       — ``GET/POST /admin/content/marketing``

All template/marketing storage is intentionally lightweight for Phase 4b —
the source of truth lives in module-level dicts (in-memory + writable via
form). Phase 5 will move these to a ``content_blocks`` DB table.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from flask import (
    Response,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import and_, func, select

from review_app.admin import _session as _admin_session
from review_app.auth.decorators import requires_role

if TYPE_CHECKING:
    from flask import Blueprint
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# In-memory store of editable content (Phase 5 promotes this to DB)
# ---------------------------------------------------------------------------
EMAIL_TEMPLATE_KINDS: Final[tuple[str, ...]] = (
    "order_confirmed",
    "in_production",
    "shipped",
    "delivered",
    "refunded",
    "problem",
)

# In-memory editable templates. Replaced wholesale on each POST.
_email_templates: dict[str, dict[str, str]] = {
    kind: {
        "subject": f"[wildprint] {kind.replace('_', ' ').title()}",
        "body": (
            f"Default {kind} email body — edit me on the admin "
            f"templates page."
        ),
        "last_edited_by": "system",
    }
    for kind in EMAIL_TEMPLATE_KINDS
}

# Marketing slot store. Same in-memory pattern.
MARKETING_SLOTS: Final[tuple[str, ...]] = (
    "homepage_hero",
    "about_us",
    "faq",
)
_marketing_content: dict[str, str] = {
    slot: f"Default copy for {slot}. Edit on /admin/content/marketing."
    for slot in MARKETING_SLOTS
}


def _send_counts_by_kind(session: Session) -> dict[str, int]:
    """Aggregate ``outbox`` row counts per ``email.<kind>`` row."""
    from review_app.email.outbox import OutboxEntry

    stmt = (
        select(OutboxEntry.kind, func.count(OutboxEntry.id))
        .where(OutboxEntry.kind.like("email.%"))
        .group_by(OutboxEntry.kind)
    )
    out: dict[str, int] = {}
    for kind, n in session.execute(stmt).all():
        # Strip the "email." prefix for display alignment with the templates
        # table, which uses bare kinds.
        bare = kind.removeprefix("email.") if isinstance(kind, str) else ""
        out[bare] = int(n or 0)
    return out


# ---------------------------------------------------------------------------
# Route registrar
# ---------------------------------------------------------------------------
def register(admin_bp: Blueprint) -> None:
    """Register all Content views on ``admin_bp``."""

    # -----------------------------------------------------------------
    # GET/POST /admin/content/email-templates — list + edit + test send
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/content/email-templates",
        methods=["GET", "POST"],
        endpoint="content_email_templates",
    )
    @requires_role("admin")
    def content_email_templates() -> Response:
        session = _admin_session.get_session()
        try:
            error: str | None = None
            if request.method == "POST":
                action = request.form.get("action") or ""
                if action == "save":
                    kind = request.form.get("kind") or ""
                    if kind not in EMAIL_TEMPLATE_KINDS:
                        error = f"unknown kind: {kind}"
                    else:
                        _email_templates[kind] = {
                            "subject": (request.form.get("subject") or "").strip(),
                            "body": (request.form.get("body") or "").strip(),
                            "last_edited_by": (
                                request.form.get("editor") or "admin"
                            ),
                        }
                elif action == "test_send":
                    kind = request.form.get("kind") or ""
                    recipient = (request.form.get("recipient") or "").strip()
                    if not recipient or "@" not in recipient:
                        error = "recipient must be an email address"
                    elif kind not in EMAIL_TEMPLATE_KINDS:
                        error = f"unknown kind: {kind}"
                    else:
                        _enqueue_test_email(session, kind=kind, recipient=recipient)
                        _admin_session.close_session_if_owned(session, commit=True)
                        return cast(
                            "Response",
                            redirect(url_for("admin.content_email_templates")),
                        )
                else:
                    error = f"unknown action: {action}"

            send_counts = _send_counts_by_kind(session)
            html = render_template(
                "admin/content/email_templates.html",
                kinds=EMAIL_TEMPLATE_KINDS,
                templates=_email_templates,
                send_counts=send_counts,
                error=error,
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET/POST /admin/content/email-log — Resend delivery audit
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/content/email-log",
        methods=["GET", "POST"],
        endpoint="content_email_log",
    )
    @requires_role("admin", "staff", "viewer")
    def content_email_log() -> Response:
        from review_app.email.outbox import OutboxEntry

        session = _admin_session.get_session()
        try:
            if request.method == "POST":
                _resend_outbox_row(session, request.form)
                _admin_session.close_session_if_owned(session, commit=True)
                return cast(
                    "Response",
                    redirect(url_for("admin.content_email_log")),
                )

            template_filter = request.args.get("template") or None
            status_filter = request.args.get("status") or None

            stmt = select(OutboxEntry).where(OutboxEntry.kind.like("email.%"))
            filters: list[Any] = []
            if template_filter:
                filters.append(OutboxEntry.kind == f"email.{template_filter}")
            if status_filter:
                filters.append(OutboxEntry.status == status_filter)
            if filters:
                stmt = stmt.where(and_(*filters))
            stmt = stmt.order_by(OutboxEntry.created_at.desc()).limit(200)
            rows = list(session.execute(stmt).scalars().all())

            html = render_template(
                "admin/content/email_log.html",
                rows=rows,
                kinds=EMAIL_TEMPLATE_KINDS,
                statuses=("pending", "sending", "sent", "failed", "dead"),
                filters={
                    "template": template_filter or "",
                    "status": status_filter or "",
                },
            )
            return make_response(html)
        finally:
            _admin_session.close_session_if_owned(session, commit=False)

    # -----------------------------------------------------------------
    # GET/POST /admin/content/marketing — homepage / about / FAQ slots
    # -----------------------------------------------------------------
    @admin_bp.route(
        "/content/marketing",
        methods=["GET", "POST"],
        endpoint="content_marketing",
    )
    @requires_role("admin")
    def content_marketing() -> Response:
        if request.method == "POST":
            for slot in MARKETING_SLOTS:
                value = request.form.get(slot)
                if value is not None:
                    _marketing_content[slot] = value.strip()
            return cast(
                "Response",
                redirect(url_for("admin.content_marketing")),
            )

        return make_response(
            render_template(
                "admin/content/marketing.html",
                slots=MARKETING_SLOTS,
                content=_marketing_content,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _enqueue_test_email(session: Session, *, kind: str, recipient: str) -> None:
    """Enqueue a one-off outbox row for a test send."""
    from review_app.email.outbox import enqueue

    template = _email_templates[kind]
    enqueue(
        session,
        kind=f"email.{kind}",
        to=recipient,
        payload={
            "subject": template["subject"],
            "body": template["body"],
            "test_send": True,
        },
    )


def _resend_outbox_row(session: Session, form: Any) -> None:
    """Re-enqueue an existing outbox row by id (POST from email-log)."""
    from review_app.email.outbox import OutboxEntry, enqueue

    raw = (form.get("resend_id") or "").strip()
    if not raw.isdigit():
        return
    row_id = int(raw)
    row = session.get(OutboxEntry, row_id)
    if row is None:
        return
    enqueue(
        session,
        kind=row.kind,
        to=(row.payload or {}).get("to", ""),
        payload={**(row.payload or {}), "resent_from_id": row.id},
    )


# Quiet linter
_ = (flash,)


__all__ = [
    "EMAIL_TEMPLATE_KINDS",
    "MARKETING_SLOTS",
    "register",
]
