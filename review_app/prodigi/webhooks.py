"""Prodigi webhook receiver — Flask blueprint at ``/webhook/prodigi``.

Per Prodigi's docs (https://www.prodigi.com/print-api/docs/reference/#callbacks),
callbacks follow the CloudEvents v1.0 spec. They are NOT cryptographically
signed; the documented integration pattern is:

1. Receive the payload, deduplicate by ``id`` (the CloudEvents event id).
2. **Re-fetch** the order via GET /v4.0/Orders/{id} before mutating local
   state — never trust the body of the callback for write operations.
3. Always 200 OK quickly so Prodigi doesn't queue retries against us.

Because we don't trust the payload, the route does the dedupe insert
synchronously and offloads the re-fetch + mutation to a queued background
job (:func:`process_callback_job`).

Order of operations on each POST:

* Parse JSON body.
* Validate the CloudEvents envelope (Pydantic).
* Insert a row in ``prodigi_callbacks`` keyed on ``event_id`` (UNIQUE).
  IntegrityError on the unique constraint = duplicate; we return 200 OK
  without doing more work.
* If the insert succeeded, enqueue :func:`process_callback_job` against
  RQ if the queue module is wired up. Falls back to running the job
  synchronously when no queue is available (acceptable for sandbox /
  unit tests).
* Return ``{"status": "accepted", "callback_id": ...}``.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from review_app.observability import get_logger
from review_app.prodigi.client import ProdigiClient, ProdigiClientError, get_default_client
from review_app.prodigi.db_models import (
    ProdigiCallback,
    ProdigiOrder,
    Shipment,
)
from review_app.prodigi.models import CallbackPayload, Order

_log = get_logger("prodigi.webhooks")


prodigi_bp = Blueprint(
    "prodigi",
    __name__,
    url_prefix="/webhook/prodigi",
)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@prodigi_bp.post("")
def receive_callback() -> Any:
    """POST /webhook/prodigi — accept a CloudEvents callback.

    Returns 200 OK with a JSON body on every documented path so Prodigi
    doesn't retry. Returns 400 only on truly malformed JSON / missing event
    id, where retrying wouldn't help.
    """
    try:
        body = request.get_json(force=True, silent=False)
    except Exception as exc:
        _log.warning("prodigi_callback_bad_json", error=type(exc).__name__)
        return jsonify({"error": "invalid_json"}), 400

    if not isinstance(body, dict):
        return jsonify({"error": "expected_object"}), 400

    try:
        payload = CallbackPayload.model_validate(body)
    except ValidationError as exc:
        _log.warning("prodigi_callback_validation_failed", error=str(exc))
        return jsonify({"error": "invalid_envelope"}), 400

    callback_id = _persist_callback(payload, body)
    if callback_id is None:
        # Duplicate — already processed (or in flight).
        _log.info(
            "prodigi_callback_duplicate",
            event_id=payload.id,
            event_type=payload.type,
        )
        return jsonify({"status": "duplicate", "event_id": payload.id}), 200

    _enqueue_processing(callback_id)
    _log.info(
        "prodigi_callback_accepted",
        event_id=payload.id,
        event_type=payload.type,
        callback_id=callback_id,
    )
    return jsonify(
        {"status": "accepted", "event_id": payload.id, "callback_id": callback_id}
    ), 200


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist_callback(payload: CallbackPayload, raw_body: dict[str, Any]) -> int | None:
    """Insert a ``prodigi_callbacks`` row. Return its id, or None on dupe."""
    from review_app.db import get_session_factory

    session_factory = get_session_factory()
    session = session_factory()
    try:
        cb = ProdigiCallback(
            event_id=payload.id,
            event_type=payload.type,
            prodigi_order_id=payload.prodigi_order_id(),
            raw_payload=raw_body,
        )
        session.add(cb)
        session.commit()
        return cb.id
    except IntegrityError:
        session.rollback()
        return None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------
def _enqueue_processing(callback_id: int) -> None:
    """Try to enqueue :func:`process_callback_job` on RQ.

    Falls back to inline execution when:

    * the ``review_app.queue`` module isn't importable
    * the env var ``PRODIGI_WEBHOOK_INLINE`` is set (testing convenience)
    * the queue isn't configured / Redis is unreachable
    """
    if os.environ.get("PRODIGI_WEBHOOK_INLINE"):
        process_callback_job(callback_id)
        return

    try:
        from review_app import queue as queue_module
    except ImportError:
        process_callback_job(callback_id)
        return

    enqueue = getattr(queue_module, "enqueue", None)
    if enqueue is None:
        process_callback_job(callback_id)
        return

    try:
        enqueue(process_callback_job, callback_id)
    except Exception as exc:
        _log.warning(
            "prodigi_callback_enqueue_failed_running_inline",
            error=type(exc).__name__,
            callback_id=callback_id,
        )
        process_callback_job(callback_id)


def process_callback_job(
    callback_id: int,
    *,
    client: ProdigiClient | None = None,
) -> None:
    """Background worker entry point.

    Re-fetches the Prodigi order, updates the local ``prodigi_orders`` row,
    upserts shipment rows, and (for completion events) emits a TODO log
    line until the outbox-backed email pipeline lands.

    Marks the callback row's ``processed_status`` to ``ok``, ``error``, or
    ``ignored`` based on the outcome.
    """
    from review_app.db import get_session_factory

    session_factory = get_session_factory()
    session = session_factory()
    try:
        cb = session.get(ProdigiCallback, callback_id)
        if cb is None:
            _log.error("prodigi_callback_missing", callback_id=callback_id)
            return

        # If we don't know which order this is for, mark and bail.
        prodigi_order_id = cb.prodigi_order_id
        if not prodigi_order_id:
            cb.processed_status = "ignored"
            cb.processed_at = datetime.now(UTC)
            cb.error_message = "no prodigi_order_id in payload"
            session.commit()
            _log.info(
                "prodigi_callback_no_order_id",
                event_id=cb.event_id,
                event_type=cb.event_type,
            )
            return

        # Re-fetch authoritative state from the API.
        api = client or get_default_client()
        try:
            order = api.get_order(prodigi_order_id)
        except ProdigiClientError as exc:
            cb.processed_status = "error"
            cb.processed_at = datetime.now(UTC)
            cb.error_message = f"refetch failed: {exc}"
            session.commit()
            _log.warning(
                "prodigi_callback_refetch_failed",
                event_id=cb.event_id,
                prodigi_order_id=prodigi_order_id,
                status_code=exc.status_code,
            )
            return

        _apply_order_to_local(session, order)
        _apply_shipments(session, order)
        _maybe_notify(cb, order)

        cb.processed_status = "ok"
        cb.processed_at = datetime.now(UTC)
        session.commit()
        _log.info(
            "prodigi_callback_processed",
            event_id=cb.event_id,
            event_type=cb.event_type,
            prodigi_order_id=prodigi_order_id,
            stage=str(order.status.stage),
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------
def _apply_order_to_local(session: Any, order: Order) -> None:
    """Upsert ``prodigi_orders`` row keyed by prodigi_order_id."""
    row = (
        session.query(ProdigiOrder)
        .filter(ProdigiOrder.prodigi_order_id == order.id)
        .one_or_none()
    )
    snapshot = order.model_dump(mode="json", by_alias=True)
    if row is None:
        # Callback can arrive before we've inserted our own row (race with
        # the create_order code path). Insert a placeholder; idempotency_key
        # is required NOT NULL — use the order's idempotency key if present,
        # else fall back to ``ord_<id>`` which is guaranteed unique.
        idempotency = order.idempotency_key or f"prodigi-{order.id}"
        row = ProdigiOrder(
            prodigi_order_id=order.id,
            idempotency_key=idempotency,
            status_stage=str(order.status.stage),
            status_details=(
                order.status.details.model_dump(mode="json", by_alias=True)
                if order.status.details
                else None
            ),
            last_fetched_at=datetime.now(UTC),
            raw_snapshot=snapshot,
        )
        session.add(row)
    else:
        row.status_stage = str(order.status.stage)
        row.status_details = (
            order.status.details.model_dump(mode="json", by_alias=True)
            if order.status.details
            else None
        )
        row.last_fetched_at = datetime.now(UTC)
        row.raw_snapshot = snapshot


def _apply_shipments(session: Any, order: Order) -> None:
    """Upsert ``shipments`` rows from the order's shipment array."""
    for sh in order.shipments:
        existing = (
            session.query(Shipment)
            .filter(Shipment.prodigi_shipment_id == sh.id)
            .one_or_none()
        )
        carrier_name = sh.carrier.name if sh.carrier else None
        carrier_service = sh.carrier.service if sh.carrier else None
        tracking_number = sh.tracking.number if sh.tracking else None
        tracking_url = sh.tracking.url if sh.tracking else None
        shipped_at = sh.dispatch_date

        if existing is None:
            session.add(
                Shipment(
                    prodigi_shipment_id=sh.id,
                    prodigi_order_id=order.id,
                    carrier_name=carrier_name,
                    carrier_service=carrier_service,
                    tracking_number=tracking_number,
                    tracking_url=tracking_url,
                    shipped_at=shipped_at,
                )
            )
        else:
            existing.prodigi_order_id = order.id
            existing.carrier_name = carrier_name
            existing.carrier_service = carrier_service
            existing.tracking_number = tracking_number
            existing.tracking_url = tracking_url
            if shipped_at is not None:
                existing.shipped_at = shipped_at


def _maybe_notify(cb: ProdigiCallback, order: Order) -> None:
    """Hook for email notifications on completion / shipment events.

    The outbox / email pipeline (Phase 0.5) lands separately; until then we
    just log a TODO for the operator. When outbox is wired in, this function
    will insert an ``outbox`` row inside the same transaction as the order
    update.
    """
    is_shipment_event = "shipment" in cb.event_type.lower()
    is_completion = order.status.stage == "Complete"
    if not (is_shipment_event or is_completion):
        return

    # The recipient email lives on the future ``orders`` table (Phase 3).
    # Until that's available we log the intent rather than enqueue half-baked
    # outbox rows that the email worker can't deliver. When Phase 3 lands,
    # this branch should resolve the customer email and call
    # ``review_app.email.outbox.enqueue(session, kind=..., to=..., payload=...)``
    # inside the same transaction as the order update.
    _log.info(
        "prodigi_notify_todo",
        event_id=cb.event_id,
        event_type=cb.event_type,
        stage=str(order.status.stage),
        note=(
            "outbox.enqueue requires customer email (Phase 3 orders table); "
            "skipping notification for now."
        ),
    )
    if current_app:
        current_app.logger.info("prodigi_notify_todo event=%s", cb.event_type)


__all__ = ["process_callback_job", "prodigi_bp"]
