"""Drop-in OpenAI SDK proxy that records telemetry on every call.

Usage (existing call sites):

    from review_app.ai import openai_client
    client = openai_client.OpenAI(api_key=KEY)
    resp = client.images.generate(model="gpt-image-1", prompt=...)

The wrapper is a thin proxy around `openai.OpenAI`. Three endpoints are
intercepted for timing + telemetry:

  * `client.chat.completions.create(...)`
  * `client.images.generate(...)`
  * `client.embeddings.create(...)`

All other attribute access falls through to the real SDK unchanged, so
adding a new endpoint to a call site doesn't require updating this
wrapper first.

Failure semantics:
  * Upstream exceptions propagate verbatim. Telemetry row is still
    written with `status='error'` + `error_class=<exception class>` when
    the feature flag is on.
  * Telemetry write failures are silenced (see `log.record_call`).
"""
from __future__ import annotations

import time
from typing import Any

from review_app.ai.log import record_call

PROVIDER = "openai"


class _CompletionsProxy:
    """Wraps `client.chat.completions` to intercept `.create(...)`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model", "unknown"))
        endpoint = "chat.completions.create"
        t0 = time.perf_counter()
        try:
            response = self._inner.create(*args, **kwargs)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            record_call(
                provider=PROVIDER,
                model=model,
                endpoint=endpoint,
                units_in=0.0,
                units_out=0.0,
                latency_ms=latency_ms,
                status="error",
                error_class=type(exc).__name__,
            )
            raise
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Pull token usage from the response if available. OpenAI's
        # response objects expose `.usage.prompt_tokens` /
        # `.completion_tokens`; we tolerate any missing attribute.
        tokens_in, tokens_out = _extract_usage(response)
        record_call(
            provider=PROVIDER,
            model=model,
            endpoint=endpoint,
            units_in=float(tokens_in),
            units_out=float(tokens_out),
            latency_ms=latency_ms,
            status="ok",
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _ChatProxy:
    """Wraps `client.chat` to intercept the `.completions` attribute."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.completions = _CompletionsProxy(inner.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _ImagesProxy:
    """Wraps `client.images` to intercept `.generate(...)`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model", "unknown"))
        n = float(kwargs.get("n", 1) or 1)
        endpoint = "images.generate"
        t0 = time.perf_counter()
        try:
            response = self._inner.generate(*args, **kwargs)
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
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _EmbeddingsProxy:
    """Wraps `client.embeddings` to intercept `.create(...)`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model", "unknown"))
        endpoint = "embeddings.create"
        t0 = time.perf_counter()
        try:
            response = self._inner.create(*args, **kwargs)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            record_call(
                provider=PROVIDER,
                model=model,
                endpoint=endpoint,
                units_in=0.0,
                units_out=0.0,
                latency_ms=latency_ms,
                status="error",
                error_class=type(exc).__name__,
            )
            raise
        latency_ms = int((time.perf_counter() - t0) * 1000)
        tokens_in, _ = _extract_usage(response)
        record_call(
            provider=PROVIDER,
            model=model,
            endpoint=endpoint,
            units_in=float(tokens_in),
            units_out=0.0,
            latency_ms=latency_ms,
            status="ok",
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class OpenAI:
    """Telemetry-wrapped replacement for `openai.OpenAI`.

    All constructor args/kwargs are forwarded to the real OpenAI client.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Lazy import so this module can be imported without the openai
        # package installed (e.g. on a worker that only ever talks to
        # Replicate). The error surfaces only when someone actually
        # constructs the client.
        from openai import OpenAI as _RealOpenAI

        self._inner = _RealOpenAI(*args, **kwargs)
        self.chat = _ChatProxy(self._inner.chat)
        self.images = _ImagesProxy(self._inner.images)
        self.embeddings = _EmbeddingsProxy(self._inner.embeddings)

    def __getattr__(self, name: str) -> Any:
        # Fall through for any endpoint not explicitly wrapped (audio,
        # files, beta APIs, etc.). These won't be logged but they'll
        # still work.
        return getattr(self._inner, name)


def _extract_usage(response: Any) -> tuple[int, int]:
    """Pull (prompt_tokens, completion_tokens) from an OpenAI response.

    Returns (0, 0) if the response shape doesn't expose usage. Never
    raises.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return (0, 0)
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        return (int(prompt), int(completion))
    except Exception:
        return (0, 0)


__all__ = ["PROVIDER", "OpenAI"]
