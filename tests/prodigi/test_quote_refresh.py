"""Unit tests for review_app.prodigi.quote_refresh.refresh_all_skus_job."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from review_app.prodigi.db_models import ProdigiSku
from review_app.prodigi.models import (
    Cost,
    CostSummary,
    Quote,
    QuoteResponse,
)


@pytest.fixture()
def patched_session_factory() -> sessionmaker[Any]:
    """Return the bound global session factory.

    The autouse ``_bind_global_session_factory`` fixture (in
    ``tests/prodigi/conftest.py``) has already rebound the global at the
    shared prodigi engine; we just hand it back here for direct use in
    test setup.
    """
    from review_app.db import get_session_factory

    return get_session_factory()  # type: ignore[no-any-return]


class TestRefreshAllSkusJob:
    def test_refresh_updates_wholesale_and_timestamp(
        self, patched_session_factory: sessionmaker[Any]
    ) -> None:
        from review_app.prodigi.quote_refresh import refresh_all_skus_job

        # Seed three active SKUs.
        with patched_session_factory() as s:
            for slug, finish in [
                ("cf-12x16-black", "Black"),
                ("cf-12x16-white", "White"),
                ("cf-12x16-natural", "Natural"),
            ]:
                s.add(
                    ProdigiSku(
                        internal_sku=slug,
                        prodigi_sku="GLOBAL-CFPM-12X16",
                        finish=finish,
                        size_inches="12x16",
                        orientation="portrait",
                        active=True,
                        in_stock=True,
                    )
                )
            s.commit()

        mock_client = MagicMock()
        mock_client.quote.return_value = QuoteResponse(
            outcome="Created",
            quotes=[
                Quote(
                    shipmentMethod="Standard",
                    costSummary=CostSummary(
                        items=Cost(amount="12.00", currency="USD"),
                        shipping=Cost(amount="6.00", currency="USD"),
                        totalCost=Cost(amount="18.00", currency="USD"),
                    ),
                ),
                Quote(
                    shipmentMethod="Budget",
                    costSummary=CostSummary(
                        totalCost=Cost(amount="14.50", currency="USD"),
                    ),
                ),
            ],
        )

        summary = refresh_all_skus_job(client=mock_client)
        assert summary["checked"] == 3
        assert summary["succeeded"] == 3
        assert summary["failed"] == 0
        # quote() was called once per SKU.
        assert mock_client.quote.call_count == 3

        with patched_session_factory() as s:
            rows = s.query(ProdigiSku).all()
            for row in rows:
                # Cheaper of (1800, 1450) is 1450.
                assert row.last_quoted_wholesale_cents == 1450
                assert row.last_refreshed_at is not None

    def test_refresh_continues_on_per_sku_failure(
        self, patched_session_factory: sessionmaker[Any]
    ) -> None:
        from review_app.prodigi.client import ProdigiClientError
        from review_app.prodigi.quote_refresh import refresh_all_skus_job

        with patched_session_factory() as s:
            s.add(
                ProdigiSku(
                    internal_sku="cf-12x16-black",
                    prodigi_sku="GLOBAL-CFPM-12X16",
                    finish="Black",
                    size_inches="12x16",
                    orientation="portrait",
                    active=True,
                    in_stock=True,
                )
            )
            s.add(
                ProdigiSku(
                    internal_sku="cf-12x16-bogus",
                    prodigi_sku="GLOBAL-CFPM-BOGUS",
                    finish="Bogus",
                    size_inches="12x16",
                    orientation="portrait",
                    active=True,
                    in_stock=True,
                )
            )
            s.commit()

        mock_client = MagicMock()

        def _fake_quote(req: Any) -> QuoteResponse:
            if any(it.sku == "GLOBAL-CFPM-BOGUS" for it in req.items):
                raise ProdigiClientError("404 not found", status_code=404)
            return QuoteResponse(
                outcome="Created",
                quotes=[
                    Quote(
                        shipmentMethod="Standard",
                        costSummary=CostSummary(
                            totalCost=Cost(amount="20.00", currency="USD"),
                        ),
                    )
                ],
            )

        mock_client.quote.side_effect = _fake_quote
        summary = refresh_all_skus_job(client=mock_client)
        assert summary["checked"] == 2
        assert summary["succeeded"] == 1
        assert summary["failed"] == 1
