"""Drop-in Replicate SDK proxy that records telemetry on every call.

Usage:

    from review_app.ai import replicate_client as replicate
    output = replicate.run(model_ref, input={...})

    # Or with an explicit client:
    client = replicate.Client(api_token=TOKEN)
    output = client.run(model_ref, input={...})

This module mirrors the public surface of the `replicate` package for the
two entry points used in wildprint: top-level `replicate.run(...)` and
`replicate.Client(...).run(...)`. Other attributes (predictions, models,
etc.) fall through to the real package unchanged.

Unit accounting: Replicate bills per second of compute. This wrapper
records `units=1.0` per call as a placeholder. Phase 5 will switch to
recording actual compute seconds from `prediction.metrics.predict_time`,
which requires moving from `client.run()` (synchronous, returns output
directly) to `client.predictions.create()` + polling (returns the
prediction object with metrics). For now the cost in `pricing.py`
encodes a per-call estimate.
"""
from __future__ import annotations

import time
from typing import Any

from review_app.ai.log import record_call

PROVIDER = "replicate"


def _model_id(model_ref: Any) -> str:
    """Best-effort extraction of a model identifier for telemetry."""
    if isinstance(model_ref, str):
        # Strip any ":<version>" suffix so per-model aggregations align
        # regardless of the pinned version.
        return model_ref.split(":")[0]
    return str(model_ref)


def _wrap_run(real_run: Any) -> Any:
    """Return a function that calls `real_run` and records telemetry."""

    def _run(model_ref: Any, *args: Any, **kwargs: Any) -> Any:
        model = _model_id(model_ref)
        endpoint = "run"
        t0 = time.perf_counter()
        try:
            output = real_run(model_ref, *args, **kwargs)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            record_call(
                provider=PROVIDER,
                model=model,
                endpoint=endpoint,
                units_in=1.0,
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
            units_in=1.0,
            units_out=0.0,
            latency_ms=latency_ms,
            status="ok",
        )
        return output

    return _run


class Client:
    """Telemetry-wrapped replacement for `replicate.Client`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from replicate import Client as _RealClient  # type: ignore[import-not-found]

        self._inner = _RealClient(*args, **kwargs)
        # Bind the wrapped run as a bound-method-like attribute. Using
        # an attribute (not a method) lets us share `_wrap_run` between
        # the module-level `run` and the per-instance `client.run`.
        self.run = _wrap_run(self._inner.run)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def run(model_ref: Any, *args: Any, **kwargs: Any) -> Any:
    """Telemetry-wrapped equivalent of `replicate.run(...)`."""
    import replicate as _real_replicate  # type: ignore[import-not-found]

    return _wrap_run(_real_replicate.run)(model_ref, *args, **kwargs)


def __getattr__(name: str) -> Any:
    """Module-level passthrough for any replicate attribute we don't wrap.

    Lets `from review_app.ai import replicate_client as replicate` then
    `replicate.predictions.create(...)` continue to work even though we
    don't intercept those endpoints (yet).
    """
    import replicate as _real_replicate  # type: ignore[import-not-found]

    return getattr(_real_replicate, name)


__all__ = ["Client", "PROVIDER", "run"]
