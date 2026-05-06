"""Transactional email module (Phase 0.5).

Public API
----------
- ``send(session, kind, to, payload)`` — high-level entry point: render
  templates and write a row to the outbox in the **caller's** SQLAlchemy
  session/transaction. The actual SMTP/HTTP send happens later, when the
  queue worker drains the outbox.
- ``render_template(kind, payload)`` — render an `(html, text)` pair for
  the given kind without touching the database.
- ``init_app(app)`` — Flask wiring stub (currently a no-op; reserved for
  future use such as registering admin email-replay routes).

Outbox pattern, briefly
-----------------------
A request handler that fires email MUST do so via ``send(session, ...)``
inside the same SQLAlchemy transaction as the business mutation
(e.g. "mark order paid"). When the transaction commits, the outbox row
becomes durable; if it rolls back, the row never existed. A separate worker
process drains the outbox via :func:`drain_outbox_job`. This eliminates the
"we charged the card but never sent the receipt" failure mode at the cost
of some send latency.

NEVER call :func:`resend_client.send_via_resend` directly from a request
handler — always go through the outbox.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from review_app.email.outbox import OutboxEntry, enqueue
from review_app.email.templates import KindNotFoundError, render_template

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.orm import Session


def send(
    session: Session,
    kind: str,
    to: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 5,
) -> OutboxEntry:
    """Enqueue a transactional email via the outbox.

    Validates `kind` by attempting to look up its template — a typo'd
    `kind` raises :class:`KindNotFoundError` immediately rather than
    surfacing as a worker-side rendering failure later.

    Parameters
    ----------
    session:
        The caller's SQLAlchemy Session. The outbox row is added (not
        committed) in this session so it participates in the caller's
        transaction.
    kind:
        Template kind, e.g. ``"email.order_confirmed"``. See
        :data:`review_app.email.templates.KIND_TO_TEMPLATE`.
    to:
        Recipient email address.
    payload:
        JSON-serializable dict of template variables. Stored verbatim in
        the outbox row; the worker re-renders at send time.
    max_attempts:
        Cap on retry attempts before status flips to ``dead``.

    Returns
    -------
    OutboxEntry
        The newly added (un-flushed) outbox row. The caller is responsible
        for committing the surrounding transaction.
    """
    # Fail fast on unknown kinds — better here than in the worker.
    if kind not in _known_kinds():
        raise KindNotFoundError(f"Unknown email kind: {kind!r}")
    return enqueue(
        session,
        kind=kind,
        to=to,
        payload=payload,
        max_attempts=max_attempts,
    )


def _known_kinds() -> frozenset[str]:
    # Late import to avoid pulling Jinja during a lightweight `import`.
    from review_app.email.templates import KIND_TO_TEMPLATE

    return frozenset(KIND_TO_TEMPLATE.keys())


def init_app(app: Flask) -> None:
    """Flask wiring stub.

    Currently a no-op. Reserved for future use (e.g. registering an admin
    email-replay blueprint, attaching a structlog binder, or surfacing
    outbox health on /healthz). The signature matches the rest of the
    Phase 0 init_app() pattern so app.py can register us alongside db,
    auth, observability, etc.

    Idempotent: safe to call multiple times.
    """
    # Phase 0 deliberately does no per-app wiring. The send/render APIs are
    # session- and config-scoped, not app-scoped.
    _ = app


__all__ = [
    "KindNotFoundError",
    "OutboxEntry",
    "init_app",
    "render_template",
    "send",
]
