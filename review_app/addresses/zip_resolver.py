"""Resolve a 5-digit US ZIP code to a 2-letter state abbreviation.

Primary path: Smarty US Street API (ZIP-only lookup is supported and returns
the standardized state). Fallback: a static ``data/zip_to_state.json`` table
keyed by ZIP prefix — used when Smarty is unreachable, returns no candidates,
or raises.

This module is used by ``/api/lead`` so the landing form (which collects ZIP)
can populate the ``state`` query param the rest of the app already expects.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from review_app.addresses.smarty import SmartyError, SmartyResult, verify_address

_log = logging.getLogger(__name__)

_ZIP_RE = re.compile(r"^\d{5}$")
_FALLBACK_PATH = Path(__file__).resolve().parents[2] / "data" / "zip_to_state.json"


class ZipResolution(TypedDict):
    """The shape of a resolve_zip return value."""

    zip_code: str
    state: str
    city: str
    source: str  # "smarty" | "fallback" | "unknown"


def is_valid_zip(zip_code: str) -> bool:
    """Strict US ZIP-5 check: exactly five digits."""
    return bool(_ZIP_RE.match(zip_code or ""))


@lru_cache(maxsize=1)
def _load_fallback_table() -> dict[str, object]:
    """Read data/zip_to_state.json once and cache it."""
    try:
        with _FALLBACK_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("zip_to_state fallback table unreadable: %s", exc)
        return {}


def fallback_lookup(zip_code: str) -> str:
    """Look up a ZIP prefix in the static table. Empty string if no match."""
    if not is_valid_zip(zip_code):
        return ""
    table = _load_fallback_table()
    prefix_map = table.get("prefix_to_state") or {}
    if isinstance(prefix_map, dict):
        hit = prefix_map.get(zip_code[:3])
        if isinstance(hit, str) and len(hit) == 2:
            return hit.upper()
    first_digit_map = table.get("_first_digit_fallback") or {}
    if isinstance(first_digit_map, dict):
        hit = first_digit_map.get(zip_code[:1])
        if isinstance(hit, str) and len(hit) == 2:
            return hit.upper()
    return ""


def resolve_zip(zip_code: str) -> ZipResolution:
    """Resolve a 5-digit ZIP to a state abbreviation.

    Tries Smarty first; falls back to the static prefix table on any error
    or empty Smarty response. Always returns a ZipResolution — the ``state``
    field will be empty only if both Smarty and the fallback fail to identify
    a state.
    """
    if not is_valid_zip(zip_code):
        return {"zip_code": zip_code, "state": "", "city": "", "source": "unknown"}

    try:
        result: SmartyResult = verify_address(
            line1="",
            line2=None,
            city="",
            state="",
            zip_code=zip_code,
        )
        state = (result.standardized_state or "").upper()
        city = result.standardized_city or ""
        if state:
            return {
                "zip_code": zip_code,
                "state": state,
                "city": city,
                "source": "smarty",
            }
        _log.info("smarty returned no state for zip=%s; using fallback", zip_code)
    except SmartyError as exc:
        _log.warning(
            "smarty unreachable for zip=%s (%s); using fallback table",
            zip_code,
            exc,
        )
    except Exception as exc:  # pragma: no cover — defensive net for unexpected errors
        _log.warning(
            "smarty raised unexpected error for zip=%s (%s); using fallback table",
            zip_code,
            exc,
        )

    state = fallback_lookup(zip_code)
    return {
        "zip_code": zip_code,
        "state": state,
        "city": "",
        "source": "fallback" if state else "unknown",
    }


__all__ = [
    "ZipResolution",
    "fallback_lookup",
    "is_valid_zip",
    "resolve_zip",
]
