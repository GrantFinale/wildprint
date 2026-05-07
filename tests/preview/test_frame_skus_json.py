"""Tests for the frame_skus.json data file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_PATH = REPO_ROOT / "data" / "frame_skus.json"
STATIC_FRAMES_DIR = REPO_ROOT / "review_app" / "static" / "frames"

REQUIRED_FIELDS = (
    "internal_sku",
    "prodigi_sku",
    "prodigi_attributes",
    "size_inches",
    "size_aspect",
    "finish_id",
    "finish_display",
    "blank_asset",
    "chevron_asset",
    "swatch_asset",
    "inner_rect_pct",
)

EXPECTED_SIZES = {"12x16", "16x20", "18x24", "24x36"}
EXPECTED_FINISHES = {
    "black",
    "white",
    "natural",
    "antique-silver",
    "antique-gold",
    "brown",
    "dark-grey",
    "light-grey",
}


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    with DATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def test_frame_skus_json_has_32_entries(catalog: list[dict]) -> None:
    assert isinstance(catalog, list)
    assert len(catalog) == 32, f"expected 32 SKUs, got {len(catalog)}"


def test_each_entry_has_required_fields(catalog: list[dict]) -> None:
    for i, entry in enumerate(catalog):
        for field in REQUIRED_FIELDS:
            assert field in entry, f"entry {i} ({entry.get('internal_sku')}) missing {field}"


def test_each_finish_present_for_all_sizes(catalog: list[dict]) -> None:
    pairs = {(e["finish_id"], e["size_inches"]) for e in catalog}
    expected = {(f, s) for f in EXPECTED_FINISHES for s in EXPECTED_SIZES}
    missing = expected - pairs
    extra = pairs - expected
    assert not missing, f"missing (finish, size) pairs: {missing}"
    assert not extra, f"unexpected (finish, size) pairs: {extra}"


def test_inner_rect_pct_within_0_100_bounds(catalog: list[dict]) -> None:
    for entry in catalog:
        rect = entry["inner_rect_pct"]
        for k in ("x", "y", "w", "h"):
            assert k in rect, f"{entry['internal_sku']}: inner_rect_pct missing {k}"
            v = rect[k]
            assert isinstance(v, (int, float)), f"{entry['internal_sku']}: rect.{k} not numeric"
            assert 0 <= v <= 100, f"{entry['internal_sku']}: rect.{k}={v} out of [0,100]"
        # And the rect must actually fit on the canvas.
        assert rect["x"] + rect["w"] <= 100.001, (
            f"{entry['internal_sku']}: x+w > 100"
        )
        assert rect["y"] + rect["h"] <= 100.001, (
            f"{entry['internal_sku']}: y+h > 100"
        )


def test_blank_asset_files_exist_on_disk(catalog: list[dict]) -> None:
    for entry in catalog:
        url = entry["blank_asset"]
        assert url.startswith("/static/frames/"), (
            f"{entry['internal_sku']}: blank_asset must be under /static/frames/"
        )
        filename = url.removeprefix("/static/frames/")
        on_disk = STATIC_FRAMES_DIR / filename
        assert on_disk.exists(), f"{entry['internal_sku']}: missing file {on_disk}"
        assert on_disk.is_file(), f"{entry['internal_sku']}: not a file {on_disk}"


def test_prodigi_attributes_use_space_separated_color_slugs(catalog: list[dict]) -> None:
    """Prodigi's color attribute is space-separated lowercase, NOT hyphenated.

    See review_app/prodigi/db_models.py and the project memory note
    ``project_prodigi_quirks``.
    """
    expected_color_for_finish = {
        "black": "black",
        "white": "white",
        "natural": "natural",
        "antique-silver": "silver",
        "antique-gold": "gold",
        "brown": "brown",
        "dark-grey": "dark grey",
        "light-grey": "light grey",
    }
    for entry in catalog:
        finish = entry["finish_id"]
        expected = expected_color_for_finish[finish]
        actual = entry["prodigi_attributes"]["color"]
        assert actual == expected, (
            f"{entry['internal_sku']}: prodigi color slug should be "
            f"'{expected}' (got '{actual}')"
        )


def test_internal_sku_format(catalog: list[dict]) -> None:
    for entry in catalog:
        sku = entry["internal_sku"]
        assert sku == f"cf-{entry['size_inches']}-{entry['finish_id']}", (
            f"internal_sku '{sku}' doesn't match cf-{{size}}-{{finish_id}} pattern"
        )


def test_prodigi_sku_format(catalog: list[dict]) -> None:
    for entry in catalog:
        size_upper = entry["size_inches"].upper()
        assert entry["prodigi_sku"] == f"GLOBAL-CFPM-{size_upper}", (
            f"prodigi_sku mismatch for {entry['internal_sku']}"
        )
