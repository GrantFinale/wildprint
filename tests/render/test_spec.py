"""Tests for :class:`RenderSpec` canonical hashing."""
from __future__ import annotations

from review_app.render.spec import RENDERER_VERSION_DEFAULT, RenderSpec


def _spec(**overrides: object) -> RenderSpec:
    base: dict[str, object] = {
        "lake": "Lake Hopatcong",
        "species": ["bass", "trout"],
        "art_style": "editorial-v1",
        "layout_config": {"grid": 3, "padding": 48},
        "renderer_version": RENDERER_VERSION_DEFAULT,
    }
    base.update(overrides)
    return RenderSpec(**base)  # type: ignore[arg-type]


def test_canonical_hash_stable_across_key_order() -> None:
    """Re-ordering ``layout_config`` keys must NOT change the hash."""
    a = _spec(layout_config={"grid": 3, "padding": 48, "margin": 24})
    b = _spec(layout_config={"margin": 24, "padding": 48, "grid": 3})
    assert a.canonical_hash() == b.canonical_hash()
    # And nested re-ordering too.
    c = _spec(layout_config={"grid": 3, "nested": {"a": 1, "b": 2}})
    d = _spec(layout_config={"nested": {"b": 2, "a": 1}, "grid": 3})
    assert c.canonical_hash() == d.canonical_hash()


def test_canonical_hash_stable_across_species_order() -> None:
    """``species`` is treated as a set — order must not change the hash."""
    a = _spec(species=["bass", "trout", "perch"])
    b = _spec(species=["perch", "bass", "trout"])
    assert a.canonical_hash() == b.canonical_hash()


def test_canonical_hash_changes_on_renderer_version_bump() -> None:
    """Bumping ``renderer_version`` MUST invalidate the hash."""
    a = _spec(renderer_version="v1")
    b = _spec(renderer_version="v2")
    assert a.canonical_hash() != b.canonical_hash()


def test_canonical_hash_includes_layout_config() -> None:
    """Changing ``layout_config`` must change the hash."""
    a = _spec(layout_config={"grid": 3})
    b = _spec(layout_config={"grid": 4})
    assert a.canonical_hash() != b.canonical_hash()


def test_canonical_hash_includes_lake_and_style() -> None:
    a = _spec(lake="Lake A")
    b = _spec(lake="Lake B")
    assert a.canonical_hash() != b.canonical_hash()

    c = _spec(art_style="editorial-v1")
    d = _spec(art_style="editorial-v2")
    assert c.canonical_hash() != d.canonical_hash()


def test_canonical_hash_is_64_hex_chars() -> None:
    """SHA-256 must produce a 64-char hex digest."""
    h = _spec().canonical_hash()
    assert len(h) == 64
    int(h, 16)  # no exception => valid hex


def test_canonical_hash_nfc_normalization() -> None:
    """NFC normalization: composed and decomposed unicode must hash identically."""
    composed = _spec(lake="café")  # café (c-a-f-e-acute)
    decomposed = _spec(lake="café")  # cafe + combining acute
    assert composed.canonical_hash() == decomposed.canonical_hash()


def test_from_legacy_kwargs_roundtrips() -> None:
    """The bridge constructor produces the same hash as the direct one."""
    direct = _spec()
    legacy = RenderSpec.from_legacy_kwargs(
        lake="Lake Hopatcong",
        species=["bass", "trout"],
        art_style="editorial-v1",
        layout_config={"grid": 3, "padding": 48},
    )
    assert direct.canonical_hash() == legacy.canonical_hash()


def test_renderer_version_default_is_v1() -> None:
    """Sanity check on the default version constant."""
    assert RENDERER_VERSION_DEFAULT == "v1"
    assert _spec().renderer_version == "v1"
