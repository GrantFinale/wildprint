"""DB write path for AI call telemetry.

`record_call` is the single entry point used by every wrapper module
(`openai_client`, `recraft_client`, `replicate_client`). It:

1. Returns immediately if `AI_LOGGING_ENABLED` is unset/false (shadow
   mode default — production stays untouched).
2. Computes cost via `pricing.compute_cost_cents`.
3. Opens a short-lived SQLAlchemy session, inserts one row, commits.
4. Catches every exception and prints it. Logging must NEVER break the
   upstream call.
"""
from __future__ import annotations

import os
import sys
import traceback
from decimal import Decimal
from typing import Optional

from review_app.ai import pricing


def _logging_enabled() -> bool:
    """Read the feature flag fresh on every call.

    We re-read on each call (rather than caching at import time) so that
    integration tests can flip the flag mid-process without restart.
    """
    raw = os.getenv("AI_LOGGING_ENABLED", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def record_call(
    provider: str,
    model: str,
    endpoint: str,
    units_in: float,
    units_out: float,
    latency_ms: int,
    status: str,
    error_class: Optional[str] = None,
    render_spec_id: Optional[str] = None,
    user_id: Optional[str] = None,
    request_hash: Optional[str] = None,
) -> None:
    """Persist one row in `ai_usage_log`.

    No-op when `AI_LOGGING_ENABLED` is unset/false. Any exception during
    cost computation, session creation, or insert is caught and printed
    to stderr — the caller never observes a failure. The upstream API
    response (or exception) propagates unchanged regardless.

    Args:
        provider: "openai" | "recraft" | "replicate".
        model: Vendor model identifier matching `pricing.PRICING` keys.
        endpoint: Logical endpoint label, e.g. "chat.completions.create".
        units_in: For LLMs, prompt tokens. For images/predictions, the
            number of units (typically 1.0).
        units_out: For LLMs, completion tokens. 0 for image / prediction
            endpoints that bill per call.
        latency_ms: Wall-clock time of the upstream call.
        status: "ok" or "error".
        error_class: Fully qualified exception class name on error.
        render_spec_id: UUID of the render spec that triggered the call.
        user_id: UUID of the authenticated user that triggered the call.
        request_hash: Deterministic hash of the request payload for
            de-duplication / replay analysis. Caller responsible.
    """
    if not _logging_enabled():
        return

    try:
        cost_cents = pricing.compute_cost_cents(
            provider=provider,
            model=model,
            units_in=units_in,
            units_out=units_out,
        )
    except Exception as exc:  # noqa: BLE001
        # Pricing must never break the call. Default to 0 and continue.
        print(
            f"[ai.log] pricing failed for {provider}/{model}: {exc}",
            file=sys.stderr,
        )
        cost_cents = 0

    try:
        # Lazy imports — avoids forcing DB module load at wrapper import
        # time, which keeps `from review_app.ai import openai_client` cheap
        # in environments without DATABASE_URL set (e.g. unit tests, lint).
        from review_app.ai.models import AIUsageLog
        from review_app.db import get_session_factory

        session_factory = get_session_factory()
        session = session_factory()
        try:
            row = AIUsageLog(
                provider=provider,
                model=model,
                endpoint=endpoint,
                render_spec_id=render_spec_id,
                user_id=user_id,
                tokens_in=int(units_in) if units_out else None,
                tokens_out=int(units_out) if units_out else None,
                units=Decimal(str(units_in)) if not units_out else None,
                cost_cents=cost_cents,
                latency_ms=latency_ms,
                status=status,
                error_class=error_class,
                request_hash=request_hash,
            )
            session.add(row)
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        # Log + swallow. Anything (DB unavailable, schema mismatch,
        # serialization failure) must NOT propagate — the upstream API
        # call has already completed (or failed) and its result/exception
        # is what the caller cares about.
        print(
            f"[ai.log] DB write failed for {provider}/{model}/{endpoint}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        # Stack trace at debug level — kept off stdout to avoid breaking
        # CLI scripts that parse subprocess output.
        if os.getenv("AI_LOGGING_DEBUG", "").strip().lower() in ("1", "true"):
            traceback.print_exc(file=sys.stderr)


__all__ = ["record_call"]
