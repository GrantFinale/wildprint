"""RQ job functions.

Job functions MUST be importable by the worker process (no lambdas, no
closures, no methods bound to per-request state). Keep them small and pure;
they should pull their own dependencies via well-known module paths.
"""

from __future__ import annotations

from datetime import UTC, datetime


def ping_job(echo: str = "pong") -> dict[str, str]:
    """No-op job that proves the queue works end-to-end.

    Returns a small dict so the test can assert the worker actually executed
    the function (vs. just enqueued it). The timestamp is UTC ISO 8601.
    """
    return {
        "echo": echo,
        "ts": datetime.now(UTC).isoformat(),
    }


def render_print_job(render_spec_id: str) -> dict[str, str]:
    """Tier-3 high-res print render. Implemented in Phase 2/3.

    Stub raises so an accidental enqueue fails loudly during Phase 0/1
    rather than silently no-oping.
    """
    raise NotImplementedError(
        f"render_print_job({render_spec_id!r}): Phase 2/3 will implement this"
    )


__all__ = ["ping_job", "render_print_job"]
