"""Object-key naming helpers.

Centralizing key construction prevents drift between the renderer, the
preview pipeline, and the post-payment print job. If we change the layout,
we change it in one place.

Sharding rationale
------------------
Each helper prefixes the content-addressable key with a 2-char hex shard
derived from the hash, e.g. ``previews/ab/abc123def....jpg``. S3-compatible
backends (DO Spaces included) partition by key prefix internally; without
the shard, a hot directory pattern (e.g. all keys under ``previews/``)
forces every request into one partition, capping list/list-pagination
throughput. The 2-char shard yields 256 evenly-distributed prefixes so
list and write throughput scale linearly with object count. AWS S3 has
documented this since 2011; DO Spaces inherits the same hashing.

Cost: zero extra storage, two extra characters per key, no impact on
fetch latency. Same trick git uses for ``.git/objects/<2>/<rest>``.
"""
from __future__ import annotations


def _shard(content_hash: str) -> str:
    """Return the 2-char shard prefix from a content hash.

    Validates that the hash has at least 2 characters; we never want to
    return an empty shard and accidentally collapse all keys into one
    prefix.
    """
    if len(content_hash) < 2:
        raise ValueError(
            f"render_spec_hash must be at least 2 chars for sharding, got {len(content_hash)}"
        )
    return content_hash[:2].lower()


def thumb_key(render_spec_hash: str) -> str:
    """Tier 1 thumbnail key — public-read, CDN-cached forever per hash.

    Layout: ``thumbs/{shard}/{hash}.jpg``
    """
    return f"thumbs/{_shard(render_spec_hash)}/{render_spec_hash}.jpg"


def preview_key(render_spec_hash: str) -> str:
    """Tier 2 preview key — public-read, watermarked, 24 h signed URL TTL.

    Layout: ``previews/{shard}/{hash}.jpg``
    """
    return f"previews/{_shard(render_spec_hash)}/{render_spec_hash}.jpg"


def print_key(order_id: str, render_spec_hash: str) -> str:
    """Tier 3 print key — private, post-payment only, signed URL handed to Prodigi.

    Layout: ``prints/{order_id}/{hash}.png``

    We key on `order_id` (not just the hash) because the print master is
    one-shot — there's no reuse benefit and grouping by order makes manual
    audit/inspection trivial (``aws s3 ls s3://posters/prints/ord_abc/``).
    """
    if not order_id:
        raise ValueError("order_id must be non-empty")
    return f"prints/{order_id}/{render_spec_hash}.png"


__all__ = ["preview_key", "print_key", "thumb_key"]
