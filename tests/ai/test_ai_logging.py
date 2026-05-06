"""Phase 0.10 — AI usage logging interceptor.

Test plan:

* Shadow mode default: with `AI_LOGGING_ENABLED` unset, `record_call` MUST
  NOT touch the database (no row inserted).
* Flag-on path: with the flag set and a working session, `record_call`
  inserts one `AIUsageLog` row with the correct cost.
* Pricing math: known (provider, model) returns the expected whole cents;
  unknown returns 0 + warns once.
* Wrapper resilience: if the DB insert raises, the upstream call still
  returns its response (or its original exception) unchanged.
* Error path: when the upstream call raises, the wrapper records
  `status='error'` AND re-raises the original exception untouched.

Postgres-only assertions (none here yet) would be marked
`@pytest.mark.integration`.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure AI_LOGGING_ENABLED is unset for the duration of the test."""
    monkeypatch.delenv("AI_LOGGING_ENABLED", raising=False)


@pytest.fixture()
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set AI_LOGGING_ENABLED=1 for the duration of the test."""
    monkeypatch.setenv("AI_LOGGING_ENABLED", "1")


@pytest.fixture()
def mock_session_factory() -> Any:
    """Patch `get_session_factory` so record_call writes to a captured mock.

    Yields the mock session instance so tests can inspect what was added.
    """
    session = MagicMock()
    factory = MagicMock(return_value=session)
    with patch("review_app.db.get_session_factory", return_value=factory):
        yield session


# ---------------------------------------------------------------------------
# record_call: shadow-mode default
# ---------------------------------------------------------------------------
def test_record_call_skipped_when_flag_off(
    flag_off: None,
    mock_session_factory: Any,
) -> None:
    """No DB activity when AI_LOGGING_ENABLED is unset."""
    from review_app.ai.log import record_call

    record_call(
        provider="openai",
        model="gpt-4o-mini",
        endpoint="chat.completions.create",
        units_in=1000.0,
        units_out=500.0,
        latency_ms=42,
        status="ok",
    )

    # Session factory should never have been invoked.
    mock_session_factory.add.assert_not_called()
    mock_session_factory.commit.assert_not_called()


# ---------------------------------------------------------------------------
# record_call: flag-on happy path
# ---------------------------------------------------------------------------
def test_record_call_inserts_row_when_flag_on(
    flag_on: None,
    mock_session_factory: Any,
) -> None:
    """One row written, with correct cost, when the flag is set."""
    from review_app.ai.log import record_call

    record_call(
        provider="openai",
        model="gpt-4o-mini",
        endpoint="chat.completions.create",
        units_in=1_000_000.0,    # 1M input tokens at $0.15/M = 15 cents
        units_out=1_000_000.0,   # 1M output tokens at $0.60/M = 60 cents
        latency_ms=123,
        status="ok",
    )

    # One add + one commit
    mock_session_factory.add.assert_called_once()
    mock_session_factory.commit.assert_called_once()
    mock_session_factory.close.assert_called_once()

    row = mock_session_factory.add.call_args.args[0]
    assert row.provider == "openai"
    assert row.model == "gpt-4o-mini"
    assert row.endpoint == "chat.completions.create"
    # 15 cents + 60 cents = 75 cents
    assert row.cost_cents == 75
    assert row.tokens_in == 1_000_000
    assert row.tokens_out == 1_000_000
    assert row.latency_ms == 123
    assert row.status == "ok"


# ---------------------------------------------------------------------------
# pricing: known model
# ---------------------------------------------------------------------------
def test_compute_cost_for_known_model() -> None:
    """Pricing math sanity check across providers."""
    from review_app.ai.pricing import compute_cost_cents

    # gpt-4o-mini: $0.15/M input, $0.60/M output
    # 2000 in + 1000 out = (2000 * 0.15 + 1000 * 0.60) / 1_000_000 USD
    #                    = (300 + 600) / 1_000_000 USD
    #                    = 0.0009 USD = 0.09 cents -> rounds to 0
    assert compute_cost_cents("openai", "gpt-4o-mini", 2000.0, 1000.0) == 0

    # 1M in + 1M out -> 15 + 60 = 75 cents
    assert (
        compute_cost_cents("openai", "gpt-4o-mini", 1_000_000.0, 1_000_000.0) == 75
    )

    # gpt-image-1: 4 cents per image, units_in=1
    assert compute_cost_cents("openai", "gpt-image-1", 1.0, 0.0) == 4

    # Recraft v3: 4 cents per image
    assert compute_cost_cents("recraft", "recraftv3", 1.0, 0.0) == 4

    # Replicate flux-schnell: ~0.3 cents per call -> rounds to 0
    assert (
        compute_cost_cents("replicate", "black-forest-labs/flux-schnell", 1.0, 0.0)
        == 0
    )


def test_compute_cost_for_unknown_model_returns_zero_and_logs_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown (provider, model) pairs return 0 and emit one stderr warning."""
    from review_app.ai import pricing
    from review_app.ai.pricing import compute_cost_cents

    # Reset the warned-set so this test is order-independent.
    pricing._WARNED_UNKNOWN.discard(("openai", "fictional-model-xyz"))

    cost = compute_cost_cents("openai", "fictional-model-xyz", 1000.0, 0.0)
    assert cost == 0

    captured = capsys.readouterr()
    assert "no pricing entry" in captured.err
    assert "fictional-model-xyz" in captured.err

    # Second call for the same key must NOT warn again.
    cost2 = compute_cost_cents("openai", "fictional-model-xyz", 999.0, 0.0)
    assert cost2 == 0
    captured2 = capsys.readouterr()
    assert captured2.err == ""


# ---------------------------------------------------------------------------
# Wrapper resilience: DB write failure must not propagate
# ---------------------------------------------------------------------------
def test_wrapper_returns_upstream_result_even_on_log_failure(
    flag_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the DB insert blows up, the wrapper still returns the upstream result."""
    # Force record_call's DB layer to explode.
    def boom_factory() -> Any:
        raise RuntimeError("DB unavailable in test")

    monkeypatch.setattr(
        "review_app.db.get_session_factory",
        boom_factory,
    )

    # Build a fake "OpenAI" instance: we monkey-patch the inner client of
    # the wrapper to an object whose images.generate returns a sentinel.
    from review_app.ai.openai_client import OpenAI

    sentinel = object()
    inner_images = MagicMock()
    inner_images.generate = MagicMock(return_value=sentinel)

    # Bypass the real openai SDK by injecting a stub _RealOpenAI.
    fake_real = MagicMock()
    fake_real.return_value.chat.completions = MagicMock()
    fake_real.return_value.images = inner_images
    fake_real.return_value.embeddings = MagicMock()
    monkeypatch.setattr(
        "openai.OpenAI",
        fake_real,
        raising=False,
    )

    client = OpenAI(api_key="sk-test")
    result = client.images.generate(model="gpt-image-1", prompt="hi", n=1)

    # Upstream sentinel returned despite the DB blowing up inside record_call.
    assert result is sentinel
    inner_images.generate.assert_called_once()


# ---------------------------------------------------------------------------
# Wrapper error path: upstream raises -> log status='error' + re-raise
# ---------------------------------------------------------------------------
def test_wrapper_logs_status_error_on_upstream_exception(
    flag_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the upstream call raises, log row has status='error' and the
    original exception still propagates."""
    captured: dict[str, Any] = {}

    def fake_record_call(**kwargs: Any) -> None:
        captured.update(kwargs)

    # Patch the symbol in the openai_client module (not log.record_call,
    # because openai_client did `from .log import record_call` at import
    # time).
    monkeypatch.setattr(
        "review_app.ai.openai_client.record_call",
        fake_record_call,
    )

    class _Boom(Exception):
        pass

    inner_images = MagicMock()
    inner_images.generate = MagicMock(side_effect=_Boom("upstream failure"))

    fake_real = MagicMock()
    fake_real.return_value.chat.completions = MagicMock()
    fake_real.return_value.images = inner_images
    fake_real.return_value.embeddings = MagicMock()
    monkeypatch.setattr("openai.OpenAI", fake_real, raising=False)

    from review_app.ai.openai_client import OpenAI

    client = OpenAI(api_key="sk-test")

    with pytest.raises(_Boom, match="upstream failure"):
        client.images.generate(model="gpt-image-1", prompt="hi", n=1)

    assert captured["status"] == "error"
    assert captured["error_class"] == "_Boom"
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-image-1"
    assert captured["endpoint"] == "images.generate"


# ---------------------------------------------------------------------------
# Postgres integration (placeholder — opt in via --integration)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_record_call_against_real_postgres(
    flag_on: None,
) -> None:
    """End-to-end: insert a row in real Postgres and read it back.

    Requires DATABASE_URL pointing at a Postgres with `ai_usage_log`
    table. Skipped unless --integration is passed.
    """
    if not os.environ.get("DATABASE_URL", "").startswith("postgres"):
        pytest.skip("DATABASE_URL not pointing at Postgres")

    from sqlalchemy import select

    from review_app.ai.log import record_call
    from review_app.ai.models import AIUsageLog
    from review_app.db import get_session

    record_call(
        provider="openai",
        model="gpt-4o-mini",
        endpoint="chat.completions.create",
        units_in=1000.0,
        units_out=500.0,
        latency_ms=99,
        status="ok",
    )

    with get_session() as session:
        rows = session.scalars(
            select(AIUsageLog)
            .where(AIUsageLog.endpoint == "chat.completions.create")
            .order_by(AIUsageLog.id.desc())
            .limit(1)
        ).all()
        assert rows
        assert rows[0].provider == "openai"
