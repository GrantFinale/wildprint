"""Thin Resend HTTP client.

Uses raw `requests` rather than the `resend` SDK for two reasons:
1. The send surface area is small (one POST), so an SDK adds dependency
   weight without much value.
2. Mocking raw HTTP via the `responses` library is friction-free in tests;
   mocking SDK internals is brittle.

Public surface:
- :func:`send_via_resend` — POST one email, return the Resend message id.
- :class:`EmailSendError` — wraps every failure mode (network, non-2xx,
  bad payload).
- :func:`get_client` — lazy singleton accessor for tests / advanced uses.

Env vars
--------
- ``RESEND_API_KEY`` — Resend send-only key (required at send time, not
  at import time).
- ``EMAIL_FROM`` — default `From:` address (e.g. ``hello@fishingposter.com``).
  Required at send time unless `from_addr` is passed explicitly.
"""
from __future__ import annotations

import os
from typing import Any, Final

import requests

RESEND_API_URL: Final[str] = "https://api.resend.com/emails"
DEFAULT_TIMEOUT_S: Final[float] = 15.0


class EmailSendError(RuntimeError):
    """Raised whenever a Resend send fails for any reason.

    The original cause (if any) is chained via ``raise ... from e`` so the
    worker's failure handler can record both the high-level message and
    the underlying exception class for debugging.
    """


# ---------------------------------------------------------------------------
# Lazy client (a thin wrapper around `requests.Session` for keep-alive).
# ---------------------------------------------------------------------------
_session_singleton: requests.Session | None = None


def get_client() -> requests.Session:
    """Return a process-wide `requests.Session`, creating it lazily.

    Does NOT read `RESEND_API_KEY` — env vars are only consulted at send
    time so that `import review_app.email.resend_client` works in any
    environment (dev shells, CI lint, mypy runs).
    """
    global _session_singleton
    if _session_singleton is None:
        _session_singleton = requests.Session()
    return _session_singleton


def reset_for_tests() -> None:
    """Clear the cached session; tests call this between cases."""
    global _session_singleton
    if _session_singleton is not None:
        _session_singleton.close()
        _session_singleton = None


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------
def send_via_resend(
    to: str,
    subject: str,
    html: str,
    text: str,
    from_addr: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> str:
    """POST one transactional email through Resend; return the message id.

    Parameters
    ----------
    to:
        Recipient address. Resend accepts a single string or a list; we
        send one-at-a-time to keep retry semantics simple.
    subject, html, text:
        Rendered email body parts.
    from_addr:
        Optional override for the `From:` header. Falls back to
        ``$EMAIL_FROM``.
    timeout:
        HTTP request timeout in seconds.

    Raises
    ------
    EmailSendError
        On missing env vars, network errors, non-2xx responses, or a
        response body that doesn't include an `id` field.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError(
            "RESEND_API_KEY environment variable is not set; "
            "cannot send transactional email."
        )
    sender = from_addr or os.environ.get("EMAIL_FROM")
    if not sender:
        raise EmailSendError(
            "EMAIL_FROM environment variable is not set and no "
            "from_addr override was provided."
        )

    payload: dict[str, Any] = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    client = get_client()
    try:
        response = client.post(
            RESEND_API_URL,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise EmailSendError(f"Resend HTTP transport error: {exc}") from exc

    if response.status_code >= 400:
        body_excerpt = response.text[:500]
        raise EmailSendError(
            f"Resend returned HTTP {response.status_code}: {body_excerpt}"
        )

    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise EmailSendError(
            f"Resend response was not valid JSON: {response.text[:500]}"
        ) from exc

    message_id = data.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise EmailSendError(
            f"Resend response missing 'id' field: {data!r}"
        )
    return message_id


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "RESEND_API_URL",
    "EmailSendError",
    "get_client",
    "reset_for_tests",
    "send_via_resend",
]
