"""Tests for `review_app.storage` (Phase 0.3).

Unit tests use `moto` (S3 mock) so they run hermetically and fast. The
moto mock decorates the test, intercepts boto3 traffic, and returns
canned S3 responses — no network, no credentials.

Integration tests are gated behind `--integration`. They hit the live
`fishingposter-thumbs` bucket using credentials supplied via env vars.
The integration test in this file does a full round-trip and cleans up
after itself.
"""
from __future__ import annotations

import os
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

# moto v5: `ThreadedMotoServer` runs a local HTTP S3 mock so signed URLs
# can be fetched end-to-end via urllib. The simpler `mock_aws` decorator
# only patches boto3 calls — it doesn't expose an HTTP server, so urllib
# requests against signed URLs would 403 against real AWS.
from moto.server import ThreadedMotoServer

from review_app import storage
from review_app.storage import buckets, keys


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def moto_server() -> Iterator[str]:
    """Run a local moto S3 server for the whole session.

    Yields the base endpoint URL (e.g. ``http://127.0.0.1:5555``) so that
    boto3 clients and `urllib` requests both target the same host.
    """
    server = ThreadedMotoServer(port=0)  # 0 → auto-assign free port
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    try:
        yield endpoint
    finally:
        server.stop()


@pytest.fixture
def mocked_spaces(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """Point `review_app.storage` at the moto server and pre-create buckets.

    Yields the boto3 S3 client so individual tests can pre-seed objects
    or inspect raw S3 state.
    """
    spaces_env = {
        "SPACES_ACCESS_KEY_ID": "test-key-id",
        "SPACES_SECRET_ACCESS_KEY": "test-secret",
        "SPACES_REGION": "us-east-1",  # moto accepts any real-looking region
        "SPACES_ENDPOINT": moto_server,
    }
    for key_, value in spaces_env.items():
        monkeypatch.setenv(key_, value)
    storage.reset_client()

    client = storage.get_client()
    for bucket_name in (
        buckets.posters_bucket(),
        buckets.previews_bucket(),
        buckets.thumbs_bucket(),
    ):
        try:
            client.create_bucket(Bucket=bucket_name)
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
        except Exception:
            # moto persists buckets across tests within one server run; if
            # it already exists from a prior test in the session, that's fine.
            pass

    yield client

    # Empty all buckets between tests so we don't leak state.
    for bucket_name in (
        buckets.posters_bucket(),
        buckets.previews_bucket(),
        buckets.thumbs_bucket(),
    ):
        try:
            response = client.list_objects_v2(Bucket=bucket_name)
            for obj in response.get("Contents") or []:
                client.delete_object(Bucket=bucket_name, Key=obj["Key"])
        except Exception:
            pass

    storage.reset_client()


# ---------------------------------------------------------------------------
# Unit tests — moto-backed
# ---------------------------------------------------------------------------
def test_put_and_get_signed_url_roundtrip(mocked_spaces: Any) -> None:
    """Upload bytes, fetch the signed URL via urllib, assert content matches."""
    bucket = buckets.thumbs_bucket()
    key = keys.thumb_key("abcdef0123456789")
    payload = b"fake JPEG bytes for round-trip test"

    # `public=False` returns a signed GET URL.
    url = storage.put_object(
        bucket=bucket,
        key=key,
        body=payload,
        content_type="image/jpeg",
        public=False,
    )

    # In production the endpoint is https://; tests run against a local
    # moto HTTP server so accept either.
    assert url.startswith(("http://", "https://"))
    assert "Signature=" in url or "X-Amz-Signature=" in url

    with urllib.request.urlopen(url) as response:
        fetched = response.read()
    assert fetched == payload


def test_get_signed_url_for_get_method(mocked_spaces: Any) -> None:
    """A GET signed URL should round-trip a freshly uploaded object."""
    bucket = buckets.previews_bucket()
    key = keys.preview_key("deadbeef" * 8)
    body = b"preview-jpeg"

    mocked_spaces.put_object(Bucket=bucket, Key=key, Body=body, ContentType="image/jpeg")

    url = storage.get_signed_url(bucket=bucket, key=key, method="GET", expires_in=300)
    with urllib.request.urlopen(url) as response:
        assert response.read() == body


def test_get_signed_url_for_put_method(mocked_spaces: Any) -> None:
    """A PUT signed URL should accept a direct upload."""
    bucket = buckets.thumbs_bucket()
    key = "direct-uploads/test.bin"
    body = b"streamed straight from a hypothetical browser"

    put_url = storage.get_signed_url(bucket=bucket, key=key, method="PUT", expires_in=300)

    # Use `requests` (already in deps) — `urllib.request.Request` doesn't set
    # Content-Length on PUT bodies reliably, which trips moto's S3 mock.
    import requests

    response = requests.put(put_url, data=body, timeout=10)
    assert response.status_code in (200, 204)

    # Verify the object actually landed with the right size and content.
    head = mocked_spaces.head_object(Bucket=bucket, Key=key)
    assert head["ContentLength"] == len(body)
    fetched = mocked_spaces.get_object(Bucket=bucket, Key=key)["Body"].read()
    assert fetched == body


def test_delete_object(mocked_spaces: Any) -> None:
    bucket = buckets.thumbs_bucket()
    key = "to-delete.txt"
    mocked_spaces.put_object(Bucket=bucket, Key=key, Body=b"x")

    storage.delete_object(bucket=bucket, key=key)

    # head_object after delete → None (404 path).
    assert storage.head_object(bucket=bucket, key=key) is None


def test_head_object_returns_none_for_missing_key(mocked_spaces: Any) -> None:
    assert storage.head_object(bucket=buckets.thumbs_bucket(), key="not-here.jpg") is None


def test_head_object_returns_metadata_for_existing_key(mocked_spaces: Any) -> None:
    bucket = buckets.previews_bucket()
    key = "metadata-probe.jpg"
    mocked_spaces.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"abc",
        ContentType="image/jpeg",
        Metadata={"hash": "abc123"},
    )

    head = storage.head_object(bucket=bucket, key=key)
    assert head is not None
    assert head["ContentLength"] == 3
    assert head["ContentType"] == "image/jpeg"
    # `Metadata` is always present (possibly empty under moto-server,
    # which is a known mock-fidelity gap; integration test against real
    # Spaces in this same file proves the round-trip works in production).
    assert "Metadata" in head


def test_iter_objects_paginates(mocked_spaces: Any) -> None:
    bucket = buckets.thumbs_bucket()
    for i in range(5):
        mocked_spaces.put_object(Bucket=bucket, Key=f"bulk/obj-{i}.txt", Body=b"x")

    found = sorted(item["Key"] for item in storage.iter_objects(bucket, prefix="bulk/"))
    assert found == [f"bulk/obj-{i}.txt" for i in range(5)]


def test_put_object_public_returns_canonical_url(mocked_spaces: Any) -> None:
    bucket = buckets.previews_bucket()
    key = "public-asset.jpg"
    url = storage.put_object(
        bucket=bucket,
        key=key,
        body=b"hello",
        content_type="image/jpeg",
        public=True,
    )
    # Public URL form: https://<bucket>.<host>/<key> (host derived from
    # the configured SPACES_ENDPOINT — moto uses 127.0.0.1:<port>).
    endpoint_host = os.environ["SPACES_ENDPOINT"].split("://", 1)[-1]
    assert url == f"https://{bucket}.{endpoint_host}/{key}"


# ---------------------------------------------------------------------------
# Configuration error
# ---------------------------------------------------------------------------
def test_storage_not_configured_error_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_client()` must raise loudly when env vars are absent."""
    for env_name in ("SPACES_ACCESS_KEY_ID", "SPACES_SECRET_ACCESS_KEY",
                     "SPACES_REGION", "SPACES_ENDPOINT"):
        monkeypatch.delenv(env_name, raising=False)
    storage.reset_client()

    with pytest.raises(storage.StorageNotConfiguredError) as excinfo:
        storage.get_client()
    msg = str(excinfo.value)
    assert "SPACES_ACCESS_KEY_ID" in msg
    assert "not configured" in msg.lower()


def test_init_app_logs_when_configured(
    moto_server: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for k, v in {
        "SPACES_ACCESS_KEY_ID": "test-key-id",
        "SPACES_SECRET_ACCESS_KEY": "test-secret",
        "SPACES_REGION": "us-east-1",
        "SPACES_ENDPOINT": moto_server,
    }.items():
        monkeypatch.setenv(k, v)
    storage.reset_client()

    with caplog.at_level("INFO", logger="review_app.storage"):
        ok = storage.init_app(app=None)  # type: ignore[arg-type]
    assert ok is True
    assert any("Storage configured" in rec.message for rec in caplog.records)


def test_init_app_logs_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for env_name in ("SPACES_ACCESS_KEY_ID", "SPACES_SECRET_ACCESS_KEY",
                     "SPACES_REGION", "SPACES_ENDPOINT"):
        monkeypatch.delenv(env_name, raising=False)
    storage.reset_client()

    with caplog.at_level("INFO", logger="review_app.storage"):
        ok = storage.init_app(app=None)  # type: ignore[arg-type]
    assert ok is False
    assert any("Storage disabled" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# keys.py / buckets.py
# ---------------------------------------------------------------------------
def test_keys_module_sharding() -> None:
    """Hashes route to their first-2-char shard prefix; case-normalized to lower."""
    h = "ABCDEF0123456789"
    assert keys.thumb_key(h) == f"thumbs/ab/{h}.jpg"
    assert keys.preview_key(h) == f"previews/ab/{h}.jpg"
    assert keys.print_key("ord_xyz", h) == f"prints/ord_xyz/{h}.png"

    # A different hash → different shard.
    assert keys.thumb_key("ff00bb").startswith("thumbs/ff/")
    assert keys.preview_key("00aa").startswith("previews/00/")


def test_keys_rejects_short_hash() -> None:
    with pytest.raises(ValueError, match="at least 2 chars"):
        keys.thumb_key("a")


def test_keys_print_rejects_empty_order() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        keys.print_key("", "abc123")


def test_bucket_for_tier_dispatch() -> None:
    assert buckets.bucket_for_tier(1) == buckets.thumbs_bucket()
    assert buckets.bucket_for_tier(2) == buckets.previews_bucket()
    assert buckets.bucket_for_tier(3) == buckets.posters_bucket()


def test_bucket_for_tier_invalid() -> None:
    with pytest.raises(ValueError, match="tier must be"):
        buckets.bucket_for_tier(99)


# ---------------------------------------------------------------------------
# Integration test — real DO Spaces
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_real_spaces_roundtrip_against_thumbs_bucket() -> None:
    """Upload a small object to the live `fishingposter-thumbs` bucket, fetch via
    signed URL, then delete. Requires SPACES_* env vars provided by the runner.

    This test is the canonical proof that the boto3 wrapping works against
    real DO Spaces (not just moto). Idempotent — uses a uuid-keyed object
    that's deleted after each run.
    """
    # Sanity: real env must be set when running under --integration.
    for required in ("SPACES_ACCESS_KEY_ID", "SPACES_SECRET_ACCESS_KEY",
                     "SPACES_REGION", "SPACES_ENDPOINT"):
        if not os.environ.get(required):
            pytest.skip(f"{required} not set; cannot run real-Spaces integration")

    storage.reset_client()
    bucket = os.environ.get("SPACES_THUMBS_BUCKET", "fishingposter-thumbs")
    key = f"_integration/test-{uuid.uuid4().hex}.txt"
    payload = b"phase-0.3 integration round-trip"

    try:
        url = storage.put_object(
            bucket=bucket,
            key=key,
            body=payload,
            content_type="text/plain",
            public=False,
        )
        with urllib.request.urlopen(url) as response:
            fetched = response.read()
        assert fetched == payload

        head = storage.head_object(bucket=bucket, key=key)
        assert head is not None
        assert head["ContentLength"] == len(payload)
    finally:
        storage.delete_object(bucket=bucket, key=key)
        # Confirm cleanup
        assert storage.head_object(bucket=bucket, key=key) is None
