"""Unit tests for the Prodigi webhook receiver."""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from review_app.prodigi import init_app as init_prodigi
from review_app.prodigi.db_models import (
    ProdigiCallback,
    ProdigiOrder,
    Shipment,
)
from review_app.prodigi.models import OrderResponse
from review_app.prodigi.webhooks import process_callback_job


@pytest.fixture()
def webhook_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Minimal Flask app with the prodigi webhook blueprint mounted.

    The autouse ``_bind_global_session_factory`` fixture from
    ``tests/prodigi/conftest.py`` rebinds ``review_app.db`` at the
    prodigi shared in-memory engine so the route's writes and the test's
    verification queries see the same data. We just need to set the
    inline-processing env flag and mount the blueprint.
    """
    monkeypatch.setenv("PRODIGI_WEBHOOK_INLINE", "1")
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_prodigi(app)
    return app


def _post_callback(app: Flask, body: dict[str, Any]) -> Any:
    client = app.test_client()
    return client.post("/webhook/prodigi", json=body)


class TestDedupe:
    def test_callback_dedupe_by_event_id(
        self,
        webhook_app: Flask,
        callback_inprogress_fixture: dict[str, Any],
        order_created_fixture: dict[str, Any],
    ) -> None:
        with patch("review_app.prodigi.webhooks.get_default_client") as mock_factory:
            order = OrderResponse.model_validate(order_created_fixture).order
            mock_client = MagicMock()
            mock_client.get_order.return_value = order
            mock_factory.return_value = mock_client

            r1 = _post_callback(webhook_app, callback_inprogress_fixture)
            assert r1.status_code == 200
            assert r1.get_json()["status"] == "accepted"

            r2 = _post_callback(webhook_app, callback_inprogress_fixture)
            assert r2.status_code == 200
            assert r2.get_json()["status"] == "duplicate"

        # Only one callback row was inserted.
        from review_app.db import get_session_factory

        with get_session_factory()() as s:
            rows = s.query(ProdigiCallback).all()
            assert len(rows) == 1
            assert rows[0].event_id == "evt_abc123"

    def test_invalid_envelope_returns_400(self, webhook_app: Flask) -> None:
        # Missing required ``id`` and ``type`` fields.
        r = _post_callback(webhook_app, {"hello": "world"})
        assert r.status_code == 400


class TestRefetch:
    def test_callback_triggers_get_order_refetch(
        self,
        webhook_app: Flask,
        callback_inprogress_fixture: dict[str, Any],
        order_created_fixture: dict[str, Any],
    ) -> None:
        with patch("review_app.prodigi.webhooks.get_default_client") as mock_factory:
            order = OrderResponse.model_validate(order_created_fixture).order
            mock_client = MagicMock()
            mock_client.get_order.return_value = order
            mock_factory.return_value = mock_client

            r = _post_callback(webhook_app, callback_inprogress_fixture)
            assert r.status_code == 200
            mock_client.get_order.assert_called_once_with("ord_1234567")

        from review_app.db import get_session_factory

        with get_session_factory()() as s:
            cb = s.query(ProdigiCallback).one()
            assert cb.processed_status == "ok"
            order_row = s.query(ProdigiOrder).one()
            assert order_row.prodigi_order_id == "ord_1234567"
            assert order_row.status_stage == "InProgress"

    def test_shipment_event_upserts_shipment_row(
        self,
        webhook_app: Flask,
        callback_shipment_fixture: dict[str, Any],
        order_complete_fixture: dict[str, Any],
    ) -> None:
        with patch("review_app.prodigi.webhooks.get_default_client") as mock_factory:
            order = OrderResponse.model_validate(order_complete_fixture).order
            mock_client = MagicMock()
            mock_client.get_order.return_value = order
            mock_factory.return_value = mock_client

            r = _post_callback(webhook_app, callback_shipment_fixture)
            assert r.status_code == 200

        from review_app.db import get_session_factory

        with get_session_factory()() as s:
            ships = s.query(Shipment).all()
            assert len(ships) == 1
            assert ships[0].prodigi_shipment_id == "shp_AAAA"
            assert ships[0].carrier_name == "USPS"
            assert ships[0].tracking_number == "9400111111111111111111"


class TestErrorPaths:
    def test_callback_unknown_event_type_logged_and_200(
        self,
        webhook_app: Flask,
        order_created_fixture: dict[str, Any],
    ) -> None:
        unknown = {
            "specversion": "1.0",
            "id": "evt_unknown_xyz",
            "type": "com.prodigi.future.invented.event",
            "source": "https://api.prodigi.com",
            "subject": "ord_1234567",
            "time": "2024-09-01T10:05:00.000Z",
            "datacontenttype": "application/json",
            "data": {"order": {"id": "ord_1234567"}},
        }
        with patch("review_app.prodigi.webhooks.get_default_client") as mock_factory:
            order = OrderResponse.model_validate(order_created_fixture).order
            mock_client = MagicMock()
            mock_client.get_order.return_value = order
            mock_factory.return_value = mock_client

            r = _post_callback(webhook_app, unknown)
            assert r.status_code == 200

    def test_refetch_failure_marks_callback_error(
        self,
        webhook_app: Flask,
        callback_inprogress_fixture: dict[str, Any],
    ) -> None:
        from review_app.prodigi.client import ProdigiClientError

        with patch("review_app.prodigi.webhooks.get_default_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.get_order.side_effect = ProdigiClientError(
                "boom", status_code=503
            )
            mock_factory.return_value = mock_client

            r = _post_callback(webhook_app, callback_inprogress_fixture)
            assert r.status_code == 200

        from review_app.db import get_session_factory

        with get_session_factory()() as s:
            cb = s.query(ProdigiCallback).one()
            assert cb.processed_status == "error"
            assert cb.error_message is not None
            assert "boom" in cb.error_message

    def test_no_order_id_marks_ignored(self, webhook_app: Flask) -> None:
        body = {
            "specversion": "1.0",
            "id": "evt_no_order",
            "type": "com.prodigi.system.something",
            "source": "https://api.prodigi.com",
            "time": "2024-09-01T10:05:00.000Z",
            "datacontenttype": "application/json",
            "data": {},
        }
        r = _post_callback(webhook_app, body)
        assert r.status_code == 200

        from review_app.db import get_session_factory

        with get_session_factory()() as s:
            cb = s.query(ProdigiCallback).one()
            assert cb.processed_status == "ignored"


class TestProcessCallbackJobDirect:
    def test_process_callback_job_handles_missing_row(self) -> None:
        # Should be a no-op if the callback id doesn't exist.
        process_callback_job(99999999, client=MagicMock())
