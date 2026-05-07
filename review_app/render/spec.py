"""Content-addressable :class:`RenderSpec` — the canonical poster recipe.

The spec hash is a SHA-256 of the canonicalized JSON serialization of all
inputs that materially affect the rendered output. Two specs that produce
identical pixels MUST hash identically; two specs that produce different
pixels MUST hash differently.

The hash is the cache key in ``render_outputs``. Any change that affects the
pixels (e.g. a renderer logic change) is forced through ``renderer_version``,
which IS part of the hash — bumping it invalidates the cache without manual
key surgery.

Notes on canonicalization
-------------------------
* ``json.dumps(..., sort_keys=True, separators=(',', ':'))`` — sorted keys,
  no whitespace. Re-ordering ``layout_config`` keys does not change the hash.
* ``species`` is sorted before hashing — the renderer treats the list as
  unordered, so ["bass", "trout"] and ["trout", "bass"] are the same poster.
* All strings are NFC-normalized to dodge "café" vs "café" hash mismatches.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumping this invalidates ALL cached tier outputs. Bump when:
#
# * The renderer's pixel output changes (font swap, layout tweak, watermark
#   placement, color correction, anything visible).
# * The tier downscale algorithm changes (BICUBIC -> LANCZOS, JPEG quality
#   change, watermark opacity, etc.).
#
# Do NOT bump for non-pixel changes (logging, caching internals, type hints).
# The expected commit-message convention is::
#
#     phase N: bump renderer_version to vX (reason)
#
# See ``docs/render-tiers.md`` for the bump checklist.
RENDERER_VERSION_DEFAULT: str = "v1"


def _nfc(value: str) -> str:
    """NFC-normalize so visually-identical strings hash identically."""
    return unicodedata.normalize("NFC", value)


def _normalize(value: Any) -> Any:
    """Recursively NFC-normalize all string leaves in a JSON-like structure."""
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize(v) for v in value]
    return value


class RenderSpec(BaseModel):
    """Canonical poster recipe — hashable, serializable, deterministic.

    Two specs hash to the same ``spec_hash`` iff their canonical_inputs are
    structurally equal (after key sort + NFC normalization + species sort).
    """

    model_config = ConfigDict(
        frozen=True,  # mirror the content-addressable contract: specs don't mutate
        extra="forbid",  # typo in a field name should fail loud, not silently
    )

    lake: str = Field(..., description="The lake / waterway name. NFC-normalized.")
    species: list[str] = Field(
        default_factory=list,
        description="List of species slugs. Order is ignored for hashing.",
    )
    art_style: str = Field(..., description="Art style identifier, e.g. 'editorial-v3'.")
    layout_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Layout-engine kwargs. Keys must be JSON-serializable scalars / lists / dicts.",
    )
    renderer_version: str = Field(
        default=RENDERER_VERSION_DEFAULT,
        description="Bump to invalidate the entire cache. Part of the hash.",
    )

    @field_validator("lake", "art_style", "renderer_version")
    @classmethod
    def _strip_and_nfc(cls, value: str) -> str:
        v = _nfc(value).strip()
        if not v:
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("species")
    @classmethod
    def _normalize_species(cls, value: list[str]) -> list[str]:
        # Don't sort here — sorting happens at hash time so that the model
        # round-trips as the user supplied it (useful for debugging).
        return [_nfc(s).strip() for s in value if s and s.strip()]

    # ------------------------------------------------------------------ public

    def canonical_dict(self) -> dict[str, Any]:
        """Return the canonical dict used for hashing.

        * String fields are NFC-normalized.
        * ``species`` is sorted (the renderer treats it as a set).
        * Nested dicts/lists in ``layout_config`` are normalized but key order
          is left alone — JSON serialization sorts keys.
        """
        return {
            "lake": _nfc(self.lake),
            "species": sorted(_nfc(s) for s in self.species),
            "art_style": _nfc(self.art_style),
            "layout_config": _normalize(self.layout_config),
            "renderer_version": _nfc(self.renderer_version),
        }

    def canonical_json(self) -> str:
        """Return the canonical JSON string. Stable across Python runs."""
        return json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def canonical_hash(self) -> str:
        """Return the SHA-256 hex digest of the canonical JSON. 64 chars."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ bridges

    @classmethod
    def from_legacy_kwargs(
        cls,
        *,
        lake: str,
        species: list[str] | None = None,
        art_style: str = "editorial-v1",
        layout_config: dict[str, Any] | None = None,
        renderer_version: str = RENDERER_VERSION_DEFAULT,
    ) -> Self:
        """Build a :class:`RenderSpec` from existing renderer-call-site kwargs.

        Phase 2 ships before the route handlers are migrated; this is the
        bridge they'll use as we cut them over.
        """
        return cls(
            lake=lake,
            species=species or [],
            art_style=art_style,
            layout_config=layout_config or {},
            renderer_version=renderer_version,
        )


__all__ = [
    "RENDERER_VERSION_DEFAULT",
    "RenderSpec",
]
