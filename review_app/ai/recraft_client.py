"""Recraft v3 telemetry helper.

Recraft has no official Python SDK — `providers/recraft_provider.py`
talks to the API via raw `urllib.request`. To avoid invasive changes to
that file we provide two integration points here:

  1. `track(model, endpoint=...)` — context manager for timing + telemetry.
     Wrap the provider's `urlopen(...)` call site with::

         from review_app.ai import recraft_client
         with recraft_client.track(model="recraftv3"):
             with urllib.request.urlopen(req, timeout=120) as resp:
                 payload = _json.loads(resp.read().decode("utf-8"))

  2. `record(model, endpoint, units, latency_ms, status, error_class=None)`
     — the explicit equivalent for callers that already manage their own
     timing.

Both forms route into `review_app.ai.log.record_call` with provider
"recraft", units=1 per call (Recraft bills per generated image), and
status='ok'/'error' based on whether the wrapped block raised.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from review_app.ai.log import record_call

PROVIDER = "recraft"


@contextmanager
def track(
    model: str = "recraftv3",
    endpoint: str = "images.generate",
    n: float = 1.0,
) -> Iterator[None]:
    """Time the wrapped block and log one telemetry row on exit.

    Records `status='error'` + `error_class=<name>` if the block raises;
    the exception then propagates unchanged. Records `status='ok'` if it
    completes normally. Logging failures are swallowed by `record_call`.

    Args:
        model: Recraft model identifier (default "recraftv3").
        endpoint: Logical endpoint label written to the log.
        n: Number of images requested in this call (default 1).
    """
    t0 = time.perf_counter()
    try:
        yield
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        record_call(
            provider=PROVIDER,
            model=model,
            endpoint=endpoint,
            units_in=n,
            units_out=0.0,
            latency_ms=latency_ms,
            status="error",
            error_class=type(exc).__name__,
        )
        raise
    latency_ms = int((time.perf_counter() - t0) * 1000)
    record_call(
        provider=PROVIDER,
        model=model,
        endpoint=endpoint,
        units_in=n,
        units_out=0.0,
        latency_ms=latency_ms,
        status="ok",
    )


def record(
    model: str,
    endpoint: str,
    units: float,
    latency_ms: int,
    status: str,
    error_class: str | None = None,
) -> None:
    """Explicit recording entry point for callers managing their own timing."""
    record_call(
        provider=PROVIDER,
        model=model,
        endpoint=endpoint,
        units_in=units,
        units_out=0.0,
        latency_ms=latency_ms,
        status=status,
        error_class=error_class,
    )


__all__ = ["PROVIDER", "record", "track"]
