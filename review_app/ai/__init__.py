"""AI provider wrappers with shadow-mode telemetry.

Phase 0.10. Every OpenAI / Recraft / Replicate call routed through this
package records one row in the `ai_usage_log` table — provider, model,
endpoint, units, cost, latency, status — when the env flag
`AI_LOGGING_ENABLED` is truthy.

When the flag is unset/false (the default in production until Phase 0.11),
every wrapper still calls the upstream API and returns the result, but
the DB write is silently skipped. Wrapper failures during logging are
caught and logged via `print(...)`; the upstream call result (or
exception) always propagates unchanged.

Usage::

    from review_app.ai import openai_client, recraft_client, replicate_client

    client = openai_client.OpenAI(api_key=KEY)
    response = client.images.generate(model=..., prompt=...)

The wrapper objects expose the same surface as the upstream SDKs: any
attribute access falls through to the wrapped client. Only known
endpoints (`chat.completions.create`, `images.generate`,
`embeddings.create`, `replicate.run`, etc.) are intercepted.

`init_app(app)` is a no-op stub — the package needs no Flask wiring beyond
existing observability — but it's exposed so `review_app/app.py` can
follow the same `init_app` convention used by `auth` and `observability`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from review_app.ai import (  # noqa: F401  (re-exports)
    openai_client,
    pricing,
    recraft_client,
    replicate_client,
)
from review_app.ai.log import record_call

if TYPE_CHECKING:
    from flask import Flask


def init_app(app: "Flask") -> None:  # noqa: ARG001
    """Wire the AI package into the Flask app.

    Currently a no-op — the wrappers don't need request/teardown hooks
    because the DB session used by `record_call` is opened and closed
    per-call. Reserved for future use (e.g. metrics endpoint registration,
    observability binding).
    """
    return None


__all__ = [
    "init_app",
    "openai_client",
    "pricing",
    "recraft_client",
    "record_call",
    "replicate_client",
]
