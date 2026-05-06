"""Boto3 wrapping for DigitalOcean Spaces.

All operations route through `review_app.storage.get_client()` so they share
the lazily-constructed client and pick up env-var changes per-process.

Errors from `botocore` are translated to `StorageError` so callers keep a
single import surface.
"""
from __future__ import annotations

import logging
from typing import IO, TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator


_log = logging.getLogger(__name__)


def _client() -> Any:
    """Resolve the lazily-built S3 client. Indirection avoids circular imports."""
    from review_app.storage import get_client

    return get_client()


def _wrap_error(operation: str, exc: BaseException) -> Exception:
    """Translate a botocore exception into `StorageError` while preserving cause."""
    from review_app.storage import StorageError

    return StorageError(f"DO Spaces {operation} failed: {exc}")


# ---------------------------------------------------------------------------
# put_object
# ---------------------------------------------------------------------------
def put_object(
    bucket: str,
    key: str,
    body: bytes | IO[bytes],
    content_type: str,
    public: bool = False,
    metadata: dict[str, str] | None = None,
) -> str:
    """Upload `body` to `bucket/key`.

    Args:
        bucket: Spaces bucket name (no scheme/host).
        key: object key, e.g. ``previews/ab/abcdef....jpg``.
        body: raw bytes or a file-like object opened in binary mode.
        content_type: MIME type, e.g. ``image/jpeg``.
        public: if True, sets ``ACL=public-read`` and returns a public CDN URL.
            If False, returns a signed GET URL with the default TTL.
        metadata: arbitrary user metadata stored with the object. Keys are
            lowercased by S3 and surface as ``x-amz-meta-<key>`` headers.

    Returns:
        Public URL when `public=True`; else a signed GET URL (1 h TTL).
    """
    from botocore.exceptions import BotoCoreError, ClientError

    extra: dict[str, Any] = {"ContentType": content_type}
    if public:
        extra["ACL"] = "public-read"
    if metadata:
        extra["Metadata"] = metadata

    client = _client()
    try:
        # boto3 accepts both `bytes` and a file-like object for `Body`.
        client.put_object(Bucket=bucket, Key=key, Body=body, **extra)
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_error("put_object", exc) from exc

    if public:
        # Build the canonical Spaces public URL. `endpoint_url` looks like
        # https://nyc3.digitaloceanspaces.com → public asset URL is
        # https://<bucket>.<region>.digitaloceanspaces.com/<key>.
        endpoint = client.meta.endpoint_url.rstrip("/")
        # Strip protocol then split host
        host = endpoint.split("://", 1)[-1]
        return f"https://{bucket}.{host}/{key}"

    return get_signed_url(bucket=bucket, key=key, method="GET")


# ---------------------------------------------------------------------------
# get_signed_url
# ---------------------------------------------------------------------------
def get_signed_url(
    bucket: str,
    key: str,
    expires_in: int = 3600,
    method: Literal["GET", "PUT"] = "GET",
) -> str:
    """Generate a pre-signed URL for `bucket/key`.

    Args:
        bucket: Spaces bucket name.
        key: object key.
        expires_in: TTL in seconds (default 1 h).
        method: ``GET`` for downloads or ``PUT`` for direct browser uploads.
            ``PUT`` URLs let the browser stream bytes straight to Spaces
            without round-tripping through our app server.

    Returns:
        Fully qualified HTTPS URL with auth params baked in.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    client_method = "get_object" if method == "GET" else "put_object"
    try:
        url = _client().generate_presigned_url(
            ClientMethod=client_method,
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_error("generate_presigned_url", exc) from exc

    if not isinstance(url, str):  # pragma: no cover - botocore always returns str
        raise _wrap_error(
            "generate_presigned_url", TypeError(f"expected str, got {type(url)!r}")
        )
    return url


# ---------------------------------------------------------------------------
# delete_object
# ---------------------------------------------------------------------------
def delete_object(bucket: str, key: str) -> None:
    """Delete `bucket/key`. Idempotent — DO Spaces returns 204 even on miss."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _client().delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_error("delete_object", exc) from exc


# ---------------------------------------------------------------------------
# head_object
# ---------------------------------------------------------------------------
def head_object(bucket: str, key: str) -> dict[str, Any] | None:
    """Fetch metadata for `bucket/key`. Returns ``None`` if the object is missing.

    The dict includes (typed loosely as `Any`):
        - `ContentLength: int`
        - `ContentType: str`
        - `ETag: str`
        - `LastModified: datetime`
        - `Metadata: dict[str, str]`
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        response = _client().head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        # 404 → return None. Anything else (403, 500, etc.) is a real error.
        error_code = exc.response.get("Error", {}).get("Code", "")
        # boto3 surfaces 404 as either "404" or "NoSuchKey" depending on
        # endpoint; cover both.
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise _wrap_error("head_object", exc) from exc
    except BotoCoreError as exc:
        raise _wrap_error("head_object", exc) from exc

    # Strip boto3's `ResponseMetadata` noise; callers care about the object,
    # not the HTTP request metadata.
    response.pop("ResponseMetadata", None)
    return dict(response)


# ---------------------------------------------------------------------------
# iter_objects
# ---------------------------------------------------------------------------
def iter_objects(bucket: str, prefix: str = "") -> Iterator[dict[str, Any]]:
    """Yield object summaries under `bucket/prefix`, paginating automatically.

    Each yielded dict matches an S3 ``Contents`` entry: ``Key``, ``Size``,
    ``ETag``, ``LastModified``, ``StorageClass``.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    paginator = _client().get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for entry in page.get("Contents") or []:
                yield dict(entry)
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_error("list_objects_v2", exc) from exc


__all__ = [
    "delete_object",
    "get_signed_url",
    "head_object",
    "iter_objects",
    "put_object",
]
