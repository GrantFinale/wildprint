"""Unit tests for the Phase 0.5 transactional email + outbox stack.

Mocks all HTTP via the `responses` library. The one truly live smoke
check lives in `scripts/smoke_email.py` and is not part of pytest.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

import pytest
import responses
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from review_app.db.base import Base
from review_app.email import resend_client, send
from review_app.email.outbox import (
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    OutboxEntry,
    claim_batch,
    enqueue,
    mark_failed,
    mark_sent,
)
from review_app.email.resend_client import (
    RESEND_API_URL,
    EmailSendError,
    send_via_resend,
)
from review_app.email.templates import (
    KIND_TO_TEMPLATE,
    KindNotFoundError,
    render_subject,
    render_template,
)


# ---------------------------------------------------------------------------
# Fixtures — local sqlite engine with the outbox model registered.
#
# We don't reuse the project conftest's `db_session` because we need the
# outbox model's table specifically, and we want test isolation that
# doesn't depend on what other models register at session scope.
# ---------------------------------------------------------------------------
@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[OutboxEntry.__table__])
    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _reset_resend_client() -> Iterator[None]:
    """Clear the cached requests.Session between tests."""
    yield
    resend_client.reset_for_tests()


# ---------------------------------------------------------------------------
# Outbox enqueue
# ---------------------------------------------------------------------------
def test_enqueue_writes_outbox_row(db_session: Session) -> None:
    entry = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="customer@example.com",
        payload={"order_number": "1001"},
    )
    db_session.flush()

    assert entry.id is not None
    assert entry.status == STATUS_PENDING
    assert entry.attempts == 0
    assert entry.max_attempts == 5
    assert entry.payload["to"] == "customer@example.com"
    assert entry.payload["order_number"] == "1001"
    # next_retry_at defaults to "now" so the row is immediately eligible.
    assert entry.next_retry_at <= datetime.now(UTC)


def test_send_high_level_validates_kind(db_session: Session) -> None:
    """`send()` (the public API) must reject unknown kinds at enqueue time."""
    with pytest.raises(KindNotFoundError):
        send(
            db_session,
            kind="email.does_not_exist",
            to="customer@example.com",
            payload={},
        )


def test_send_writes_outbox_in_caller_session(db_session: Session) -> None:
    """`send()` must add to the caller's session without committing."""
    entry = send(
        db_session,
        kind="email.order_confirmed",
        to="customer@example.com",
        payload={"order_number": "X"},
    )
    # Row exists in session, not yet flushed.
    db_session.flush()
    assert entry.id is not None
    assert entry.status == STATUS_PENDING


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------
def test_render_order_confirmed_renders_with_data() -> None:
    payload = {
        "order_number": "1042",
        "customer_name": "Jane Angler",
        "line_items": [
            {
                "name": "Smallmouth Bass — Vintage",
                "size": "18x24",
                "quantity": 1,
                "price_cents": 4900,
            },
            {
                "name": "Largemouth Bass — Modern",
                "size": "24x36",
                "quantity": 2,
                "price_cents": 6900,
            },
        ],
        "total_cents": 18700,
        "currency": "USD",
    }

    subject = render_subject("email.order_confirmed", payload)
    assert "1042" in subject

    html, text = render_template("email.order_confirmed", payload)

    # All required fields appear in both representations.
    for body in (html, text):
        assert "1042" in body
        assert "Jane Angler" in body
        assert "Smallmouth Bass" in body
        assert "Largemouth Bass" in body
        assert "187.00" in body  # total formatted as dollars

    # HTML body actually looks like HTML.
    assert "<html" in html.lower()
    assert "<table" in html.lower()
    # Text body is plain text — no obvious HTML tags.
    assert "<html" not in text.lower()


def test_render_shipped_renders_tracking_fields() -> None:
    html, text = render_template(
        "email.shipped",
        {
            "order_number": "1042",
            "carrier": "USPS",
            "tracking_number": "9400111899223197123456",
            "tracking_url": "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400111899223197123456",
        },
    )
    for body in (html, text):
        assert "1042" in body
        assert "USPS" in body
        assert "9400111899223197123456" in body


def test_render_unknown_kind_raises() -> None:
    with pytest.raises(KindNotFoundError):
        render_template("email.does_not_exist", {})
    with pytest.raises(KindNotFoundError):
        render_subject("email.does_not_exist", {})


def test_all_registered_kinds_have_template_files() -> None:
    """KIND_TO_TEMPLATE entries must all resolve to real files."""
    for kind, (_subject, html_path, text_path) in KIND_TO_TEMPLATE.items():
        # Render with permissive payload — stub templates need order_number.
        payload: dict[str, Any] = {
            "order_number": "TEST",
            "customer_name": "Test",
            "line_items": [],
            "total_cents": 0,
            "currency": "USD",
            "carrier": "TEST",
            "tracking_number": "TEST",
            "tracking_url": "",
            "amount_cents": 0,
            "message": "TEST",
        }
        html, text = render_template(kind, payload)
        assert html, f"{kind}: empty html (file: {html_path})"
        assert text, f"{kind}: empty text (file: {text_path})"


# ---------------------------------------------------------------------------
# Resend HTTP client
# ---------------------------------------------------------------------------
def test_resend_client_lazy_init_no_env_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing + getting the client must not require env vars."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    # Just calling get_client() should not raise.
    client = resend_client.get_client()
    assert client is not None


def test_resend_send_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")
    with pytest.raises(EmailSendError, match="RESEND_API_KEY"):
        send_via_resend("a@b.com", "s", "<p>h</p>", "h")


def test_resend_send_missing_from_addr_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    with pytest.raises(EmailSendError, match="EMAIL_FROM"):
        send_via_resend("a@b.com", "s", "<p>h</p>", "h")


@responses.activate
def test_resend_send_with_mocked_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_abc123")
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")

    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"id": "msg_test_xyz"},
        status=200,
    )

    msg_id = send_via_resend(
        to="customer@example.com",
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
    )
    assert msg_id == "msg_test_xyz"

    # Assert auth header + payload shape.
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer re_test_key_abc123"
    assert call.request.headers["Content-Type"] == "application/json"
    body = call.request.body
    assert body is not None
    body_text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert '"from": "hello@fishingposter.com"' in body_text
    assert '"customer@example.com"' in body_text
    assert '"subject": "Hello"' in body_text


@responses.activate
def test_resend_send_4xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")

    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"error": "domain not verified"},
        status=403,
    )
    with pytest.raises(EmailSendError, match="HTTP 403"):
        send_via_resend("a@b.com", "s", "<p>h</p>", "h")


@responses.activate
def test_resend_send_missing_id_in_response_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")
    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"queued": True},
        status=200,
    )
    with pytest.raises(EmailSendError, match="missing 'id'"):
        send_via_resend("a@b.com", "s", "<p>h</p>", "h")


# ---------------------------------------------------------------------------
# Outbox claim_batch / mark_sent / mark_failed
# ---------------------------------------------------------------------------
def test_outbox_claim_batch_marks_status_sending(db_session: Session) -> None:
    """claim_batch flips status from pending → sending and respects limit."""
    for i in range(5):
        enqueue(
            db_session,
            kind="email.order_confirmed",
            to=f"c{i}@example.com",
            payload={"order_number": str(1000 + i)},
        )
    db_session.flush()

    rows = claim_batch(db_session, limit=3)
    assert len(rows) == 3
    for row in rows:
        assert row.status == STATUS_SENDING

    # Remaining 2 rows are still pending.
    pending_count = (
        db_session.query(OutboxEntry)
        .filter(OutboxEntry.status == STATUS_PENDING)
        .count()
    )
    assert pending_count == 2


def test_outbox_claim_batch_skips_future_next_retry(db_session: Session) -> None:
    """Rows with next_retry_at in the future are NOT claimed."""
    future = datetime.now(UTC) + timedelta(hours=1)
    e1 = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="a@example.com",
        payload={"order_number": "1"},
    )
    e2 = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="b@example.com",
        payload={"order_number": "2"},
    )
    db_session.flush()
    e2.next_retry_at = future
    db_session.flush()

    rows = claim_batch(db_session, limit=10)
    claimed_ids = {r.id for r in rows}
    assert e1.id in claimed_ids
    assert e2.id not in claimed_ids


def test_outbox_mark_sent_finalizes_row(db_session: Session) -> None:
    entry = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="c@example.com",
        payload={"order_number": "1"},
    )
    db_session.flush()
    claim_batch(db_session, limit=10)

    mark_sent(db_session, entry.id, "msg_abc")

    refreshed = db_session.get(OutboxEntry, entry.id)
    assert refreshed is not None
    assert refreshed.status == STATUS_SENT
    assert refreshed.sent_at is not None
    assert refreshed.payload["resend_message_id"] == "msg_abc"


def test_outbox_mark_failed_with_backoff_schedule(db_session: Session) -> None:
    """Verify each retry's next_retry_at sits in the right backoff window."""
    entry = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="c@example.com",
        payload={"order_number": "1"},
        max_attempts=5,
    )
    db_session.flush()

    expected = [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=25),
        timedelta(hours=2),
    ]

    for i, delta in enumerate(expected, start=1):
        before = datetime.now(UTC)
        mark_failed(db_session, entry.id, f"attempt {i} bombed")
        refreshed = db_session.get(OutboxEntry, entry.id)
        assert refreshed is not None
        assert refreshed.attempts == i
        assert refreshed.status == STATUS_FAILED
        # next_retry_at = "now at mark time" + backoff. Allow generous
        # tolerance for the 'now()' call inside mark_failed.
        delay = refreshed.next_retry_at - before
        assert delay >= delta - timedelta(seconds=2), (
            f"attempt {i}: delay {delay} too small (want >= {delta})"
        )
        assert delay <= delta + timedelta(seconds=10), (
            f"attempt {i}: delay {delay} too large (want <= {delta} + 10s)"
        )

    # Final attempt → status flips to dead.
    mark_failed(db_session, entry.id, "final attempt bombed")
    refreshed = db_session.get(OutboxEntry, entry.id)
    assert refreshed is not None
    assert refreshed.attempts == 5
    assert refreshed.status == STATUS_DEAD


def test_outbox_dead_rows_not_reclaimed(db_session: Session) -> None:
    """Once a row is dead it must never be claimed again."""
    entry = enqueue(
        db_session,
        kind="email.order_confirmed",
        to="c@example.com",
        payload={"order_number": "1"},
        max_attempts=1,
    )
    db_session.flush()
    mark_failed(db_session, entry.id, "kaboom")
    refreshed = db_session.get(OutboxEntry, entry.id)
    assert refreshed is not None
    assert refreshed.status == STATUS_DEAD

    rows = claim_batch(db_session, limit=10)
    assert all(r.id != entry.id for r in rows)


# ---------------------------------------------------------------------------
# drain_outbox_job — integrates queue/jobs.py + outbox + mocked Resend.
# ---------------------------------------------------------------------------
@responses.activate
def test_drain_outbox_job_processes_pending_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """End-to-end: enqueue rows, run drain_outbox_job, assert sent."""
    # Configure a fresh sqlite DB the job's `get_session` will pick up.
    db_path = tmp_path / "drain.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")

    # Reset DB module singletons so they pick up the new DATABASE_URL.
    import review_app.db as db_module

    db_module._engine = None
    db_module._session_factory = None
    db_module._scoped_session = None

    # Build the schema for our model.
    from review_app.db import get_engine

    engine = get_engine()
    Base.metadata.create_all(engine, tables=[OutboxEntry.__table__])

    # Mock Resend — return distinct ids for each call so we can assert both
    # were processed.
    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"id": "msg_one"},
        status=200,
    )
    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"id": "msg_two"},
        status=200,
    )

    # Enqueue two rows in the same DB.
    from review_app.db import get_session_factory

    SessionFactory = get_session_factory()
    with SessionFactory() as s:
        enqueue(
            s,
            kind="email.order_confirmed",
            to="alice@example.com",
            payload={
                "order_number": "1001",
                "customer_name": "Alice",
                "line_items": [
                    {
                        "name": "Bass",
                        "size": "18x24",
                        "quantity": 1,
                        "price_cents": 4900,
                    }
                ],
                "total_cents": 4900,
                "currency": "USD",
            },
        )
        enqueue(
            s,
            kind="email.shipped",
            to="bob@example.com",
            payload={
                "order_number": "1002",
                "carrier": "USPS",
                "tracking_number": "TRACK123",
                "tracking_url": "https://example.com/track",
            },
        )
        s.commit()

    # Run the job.
    from review_app.queue.jobs import drain_outbox_job

    result = drain_outbox_job(batch_size=10)
    assert result["claimed"] == 2
    assert result["sent"] == 2
    assert result["failed"] == 0

    # Both rows are now status='sent' with stamped resend ids.
    with SessionFactory() as s:
        rows = s.query(OutboxEntry).order_by(OutboxEntry.id).all()
        assert len(rows) == 2
        statuses = {r.status for r in rows}
        assert statuses == {STATUS_SENT}
        msg_ids = {r.payload["resend_message_id"] for r in rows}
        assert msg_ids == {"msg_one", "msg_two"}

    # Reset DB singletons so subsequent tests don't reuse the tmp_path engine.
    db_module._engine = None
    db_module._session_factory = None
    db_module._scoped_session = None


@responses.activate
def test_drain_outbox_job_marks_failed_on_resend_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A 500 from Resend must increment attempts + flip status=failed."""
    db_path = tmp_path / "drain_fail.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "hello@fishingposter.com")

    import review_app.db as db_module

    db_module._engine = None
    db_module._session_factory = None
    db_module._scoped_session = None

    from review_app.db import get_engine, get_session_factory

    engine = get_engine()
    Base.metadata.create_all(engine, tables=[OutboxEntry.__table__])

    responses.add(
        responses.POST,
        RESEND_API_URL,
        json={"error": "internal"},
        status=500,
    )

    SessionFactory = get_session_factory()
    with SessionFactory() as s:
        e = enqueue(
            s,
            kind="email.order_confirmed",
            to="alice@example.com",
            payload={
                "order_number": "1",
                "customer_name": "A",
                "line_items": [],
                "total_cents": 0,
                "currency": "USD",
            },
        )
        s.commit()
        entry_id = e.id

    from review_app.queue.jobs import drain_outbox_job

    result = drain_outbox_job(batch_size=10)
    assert result["sent"] == 0
    assert result["failed"] == 1

    with SessionFactory() as s:
        refreshed = s.get(OutboxEntry, entry_id)
        assert refreshed is not None
        assert refreshed.status == STATUS_FAILED
        assert refreshed.attempts == 1
        assert refreshed.last_error is not None
        assert "500" in refreshed.last_error or "EmailSendError" in refreshed.last_error

    db_module._engine = None
    db_module._session_factory = None
    db_module._scoped_session = None
