"""PII scrubbing for Sentry events and structured log payloads.

Pure functions — no I/O, no side effects, easy to unit test.

The scrubber walks an arbitrary nested dict/list structure and redacts
values whose *key* matches a known sensitive name (case-insensitive
substring match) or whose *value* matches an IPv4 address pattern.

This is intentionally conservative: false positives (over-redaction) are
much cheaper than leaking a real secret to Sentry.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

REDACTED: str = "[REDACTED]"

# Substrings (lowercased) that, when present in a dict key, mark the value
# as sensitive. Matching is `substring in key.lower()` so e.g. `user_email`,
# `X-Api-Key`, `set-cookie` all hit.
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "email",
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "cookie",
    "authorization",
)

# IPv4 dotted-quad. We deliberately do not catch IPv6 here to keep the
# regex cheap; extend later if needed.
_IPV4_RE: re.Pattern[str] = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)


def _key_is_sensitive(key: str) -> bool:
    """Return True if `key` looks like a sensitive field name."""
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _scrub_value(value: Any) -> Any:
    """Recursively scrub a value.

    - dicts: recurse, redacting sensitive keys wholesale.
    - lists/tuples: recurse element-wise (preserving the container type).
    - strings: replace any IPv4 substring with `[REDACTED]`.
    - everything else: returned unchanged.
    """
    if isinstance(value, Mapping):
        return _scrub_mapping(value)
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub_value(item) for item in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    if isinstance(value, str):
        if _IPV4_RE.search(value):
            return _IPV4_RE.sub(REDACTED, value)
        return value
    return value


def _scrub_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict with sensitive keys redacted and values recursed."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(key, str) and _key_is_sensitive(key):
            out[key] = REDACTED
        else:
            out[key] = _scrub_value(value)
    return out


def scrub_pii(
    event: MutableMapping[str, Any],
    hint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Scrub PII from a Sentry event payload.

    Designed to be plugged into ``sentry_sdk.init(before_send=scrub_pii)``.
    `hint` is accepted for signature compatibility with Sentry's
    ``before_send`` callback but is not currently consulted.

    Always returns a *new* dict — never mutates the input.
    """
    del hint  # unused; reserved for future heuristics
    return _scrub_mapping(event)


__all__ = ["REDACTED", "scrub_pii"]
