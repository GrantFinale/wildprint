"""DigitalOcean Spaces storage module (Phase 0.3).

Wraps the S3-compatible boto3 client against `https://<region>.digitaloceanspaces.com`
with a lazy connection pattern: the client is NOT instantiated at import time,
so `import review_app.storage` is safe in environments without credentials
(unit tests, ad-hoc scripts, CI lint passes).

See `docs/storage-keys.md` for bucket layout, key naming, and tier mapping.

Decision context: storage provider is DO Spaces (not Cloudflare R2). See
`memory/project_storage.md`. Same boto3 shape, different env vars.

Public API
----------
- `get_client()` — lazily build & cache the boto3 S3 client.
- `put_object()` — upload bytes/streams and return a URL.
- `get_signed_url()` — pre-signed GET or PUT URL (default 1 h TTL).
- `delete_object()` — delete a single object.
- `head_object()` — metadata fetch; returns `None` on 404.
- `iter_objects()` — paginated `list_objects_v2` iterator.
- `init_app(app)` — Flask wiring no-op stub (logs status only).
- `StorageError` / `StorageNotConfiguredError` — error hierarchy.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask
    from mypy_boto3_s3.client import S3Client


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class StorageError(Exception):
    """Base error for the storage module.

    Wraps boto3's `ClientError` / `BotoCoreError` so callers don't need to
    import boto3 to handle storage failures.
    """


class StorageNotConfiguredError(StorageError):
    """Raised when required `SPACES_*` environment variables are missing.

    Surfaced by `get_client()` on first use so callers fail loudly rather
    than silently routing to a misconfigured client. Mirrors the lazy
    pattern used by `review_app.db.get_engine()`.
    """


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------
_REQUIRED_ENV: tuple[str, ...] = (
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
    "SPACES_REGION",
    "SPACES_ENDPOINT",
)

_client_singleton: S3Client | None = None


def _resolve_env() -> dict[str, str]:
    """Read & validate the SPACES_* env vars. Raise on first missing."""
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise StorageNotConfiguredError(
            "DO Spaces storage is not configured: missing env vars "
            f"{', '.join(missing)}. Set them or use the unit-test mocked client."
        )
    return {name: os.environ[name] for name in _REQUIRED_ENV}


def _build_client() -> S3Client:
    """Construct the underlying boto3 S3 client.

    Uses `signature_version='s3v4'` (DO Spaces requires SigV4 for presigned
    URLs against custom endpoints) and disables SSL validation toggling so
    the default secure path is preserved.
    """
    import boto3
    from botocore.client import Config

    env = _resolve_env()
    client = boto3.client(
        "s3",
        region_name=env["SPACES_REGION"],
        endpoint_url=env["SPACES_ENDPOINT"],
        aws_access_key_id=env["SPACES_ACCESS_KEY_ID"],
        aws_secret_access_key=env["SPACES_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )
    return client


def get_client() -> S3Client:
    """Return the process-wide S3 client, building it on first use.

    Raises `StorageNotConfiguredError` if any required env var is missing.
    """
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = _build_client()
    return _client_singleton


def reset_client() -> None:
    """Drop the cached client. Test helper — not part of the public API."""
    global _client_singleton
    _client_singleton = None


# ---------------------------------------------------------------------------
# Operation re-exports
# ---------------------------------------------------------------------------
# Implementations live in `spaces.py` so the package's `__init__.py` stays a
# thin facade. We import them inline (not at module top) only where needed to
# avoid circular-import surprises during partial test setups.
from review_app.storage.spaces import (  # noqa: E402
    delete_object,
    get_signed_url,
    head_object,
    iter_objects,
    put_object,
)


# ---------------------------------------------------------------------------
# Flask wiring (no-op stub)
# ---------------------------------------------------------------------------
def init_app(app: Flask) -> bool:
    """Log storage status. No mutation of `app` — wiring is deferred.

    Mirrors `review_app.observability.sentry.init_sentry()`: callable in any
    environment, logs an INFO line stating whether storage is ready.

    Returns True if env is configured, False otherwise.
    """
    try:
        _resolve_env()
    except StorageNotConfiguredError:
        _log.info("Storage disabled (SPACES_* env vars not set)")
        return False
    _log.info("Storage configured")
    return True


__all__ = [
    "StorageError",
    "StorageNotConfiguredError",
    "delete_object",
    "get_client",
    "get_signed_url",
    "head_object",
    "init_app",
    "iter_objects",
    "put_object",
    "reset_client",
]
