"""Per-provider, per-model price table.

Source: vendor public pricing pages as of LAST_UPDATED. Re-verify quarterly.
Prices are stored as fractional cents (USD) so we don't lose precision on
sub-cent token rates; conversion to whole cents happens in
`compute_cost_cents`.

Schema for each PRICING entry (keys are optional — supply only what the
endpoint bills on):

    {
        # Per-token billing (LLMs, embeddings):
        "input_per_million_tokens": float,    # USD
        "output_per_million_tokens": float,   # USD
        # Per-call billing (image gen, predictions):
        "per_unit_cents": float,              # USD cents per unit (e.g. 1 image)
    }

Unknown (provider, model) pairs return cost=0 and emit one warning per
process per pair — never an exception, since we must not break the
upstream call.
"""
from __future__ import annotations

import sys
from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict

LAST_UPDATED = "2026-05-05"


class _PriceEntry(TypedDict, total=False):
    input_per_million_tokens: float
    output_per_million_tokens: float
    per_unit_cents: float


# Pricing table. Numbers are in USD.
# Sources (verified 2026-05-05):
#   - https://openai.com/api/pricing/
#   - https://www.recraft.ai/docs#api-pricing
#   - https://replicate.com/<model>/pricing
PRICING: dict[tuple[str, str], _PriceEntry] = {
    # --- OpenAI: chat / completions ---------------------------------------
    ("openai", "gpt-4o"): {
        "input_per_million_tokens": 2.50,
        "output_per_million_tokens": 10.00,
    },
    ("openai", "gpt-4o-mini"): {
        "input_per_million_tokens": 0.15,
        "output_per_million_tokens": 0.60,
    },
    ("openai", "gpt-4.1"): {
        "input_per_million_tokens": 2.00,
        "output_per_million_tokens": 8.00,
    },
    ("openai", "gpt-4.1-mini"): {
        "input_per_million_tokens": 0.40,
        "output_per_million_tokens": 1.60,
    },
    ("openai", "gpt-4.1-nano"): {
        "input_per_million_tokens": 0.10,
        "output_per_million_tokens": 0.40,
    },
    ("openai", "o1"): {
        "input_per_million_tokens": 15.00,
        "output_per_million_tokens": 60.00,
    },
    ("openai", "o3-mini"): {
        "input_per_million_tokens": 1.10,
        "output_per_million_tokens": 4.40,
    },
    # --- OpenAI: embeddings -----------------------------------------------
    ("openai", "text-embedding-3-small"): {
        "input_per_million_tokens": 0.02,
    },
    ("openai", "text-embedding-3-large"): {
        "input_per_million_tokens": 0.13,
    },
    # --- OpenAI: images ---------------------------------------------------
    # gpt-image-1 high-quality 1024x1024 is approx 4.0 cents per image at
    # the published "high" tier. Other tiers (low/medium) are cheaper but
    # we conservatively bill the "high" rate so we never under-report.
    ("openai", "gpt-image-1"): {"per_unit_cents": 4.0},
    ("openai", "dall-e-3"): {"per_unit_cents": 4.0},
    ("openai", "dall-e-2"): {"per_unit_cents": 2.0},
    # --- Recraft: images --------------------------------------------------
    # Recraft v3 lists $0.04/image for the standard tier.
    ("recraft", "recraftv3"): {"per_unit_cents": 4.0},
    # --- Replicate: image gen ---------------------------------------------
    # Replicate bills per-second; we approximate with per-call costs based
    # on observed median runtimes. Phase 5 will switch to actual seconds
    # once the wrapper records the prediction.metrics.predict_time field.
    ("replicate", "black-forest-labs/flux-schnell"): {"per_unit_cents": 0.3},
    ("replicate", "black-forest-labs/flux-1.1-pro-ultra"): {"per_unit_cents": 6.0},
    ("replicate", "nightmareai/real-esrgan"): {"per_unit_cents": 1.5},
}


# Module-level set of (provider, model) pairs we've already warned about,
# so a high-volume call site doesn't spam the log on every miss.
_WARNED_UNKNOWN: set[tuple[str, str]] = set()


def compute_cost_cents(
    provider: str,
    model: str,
    units_in: float,
    units_out: float = 0.0,
) -> int:
    """Compute call cost in whole cents.

    Args:
        provider: e.g. "openai", "recraft", "replicate".
        model: Vendor model identifier as used in PRICING keys.
        units_in: For LLMs, prompt tokens. For images/predictions, the
            number of units (e.g. images requested).
        units_out: For LLMs, completion tokens. For per-unit-billed
            endpoints, leave at 0.

    Returns:
        Cost rounded to the nearest whole cent. Unknown (provider, model)
        pairs return 0 and emit a one-shot stderr warning. Never raises —
        the upstream call must not be broken by a pricing miss.
    """
    key = (provider, model)
    entry = PRICING.get(key)
    if entry is None:
        if key not in _WARNED_UNKNOWN:
            _WARNED_UNKNOWN.add(key)
            print(
                f"[ai.pricing] WARNING: no pricing entry for "
                f"provider={provider!r} model={model!r}; "
                f"recording cost_cents=0. Add it to review_app/ai/pricing.py.",
                file=sys.stderr,
            )
        return 0

    cost = Decimal("0")

    in_rate = entry.get("input_per_million_tokens")
    if in_rate is not None and units_in:
        # USD per million tokens -> cents per token = rate * 100 / 1_000_000
        cost += (Decimal(str(in_rate)) * Decimal(str(units_in)) * Decimal("100")
                 / Decimal("1000000"))

    out_rate = entry.get("output_per_million_tokens")
    if out_rate is not None and units_out:
        cost += (Decimal(str(out_rate)) * Decimal(str(units_out)) * Decimal("100")
                 / Decimal("1000000"))

    per_unit = entry.get("per_unit_cents")
    if per_unit is not None:
        # For per-unit-billed endpoints, units_in carries the unit count
        # (default 1.0 from the wrapper).
        units = units_in if units_in else 1.0
        cost += Decimal(str(per_unit)) * Decimal(str(units))

    # Round half-up to the nearest whole cent. Costs are bounded by API
    # response sizes, so int() conversion never overflows in practice.
    return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


__all__ = ["LAST_UPDATED", "PRICING", "compute_cost_cents"]
