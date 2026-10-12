"""The built-in price table (ADR-040, ADR-044, KI-050).

Real vendor prices change often and a wrong default would make every dashboard figure quietly
wrong, so an entry is added only from a vendor's own pricing page, with its source and fetch date,
and each batch is a new `pricing_version`. Models without an entry show as unpriced; workspaces can
add overrides. Never edit a published version's prices: add a new version with a later `valid_from`.
"""

from datetime import UTC, datetime
from decimal import Decimal

from abb_api.cost.pricing import PriceBook, PriceEntry

BUILTIN_VERSION = "2026-10-01"

_V1 = datetime(2026, 10, 1, tzinfo=UTC)

_EXAMPLE_ENTRIES: tuple[PriceEntry, ...] = (
    PriceEntry(
        pricing_version=BUILTIN_VERSION,
        provider="example-provider",
        model_pattern="model-x",
        valid_from=_V1,
        input_per_million=Decimal("3.0"),
        output_per_million=Decimal("15.0"),
        cached_input_per_million=Decimal("0.3"),
        source="illustrative (spec §23 example)",
    ),
    PriceEntry(
        pricing_version=BUILTIN_VERSION,
        provider="example-provider",
        model_pattern="model-x-mini*",
        valid_from=_V1,
        input_per_million=Decimal("0.25"),
        output_per_million=Decimal("1.25"),
        source="illustrative",
    ),
)


# Version 2026-10-09: Anthropic list prices (USD per million tokens), base input, output and cache
# hit, as quoted in the model pricing table on platform.claude.com/docs/en/about-claude/pricing and
# matching claude.com/pricing, both read 2026-10-09. Not modelled: batch (50%), fast mode, US-only
# inference (1.1x) and cache writes.
# Haiku 5.5 and the OpenAI models were added in version 2026-10-12 below.
ANTHROPIC_VERSION = "2026-10-09"
_V2 = datetime(2026, 10, 9, tzinfo=UTC)
_ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing (read 2026-10-09)"

ANTHROPIC_ENTRIES: tuple[PriceEntry, ...] = tuple(
    PriceEntry(
        pricing_version=ANTHROPIC_VERSION,
        provider="anthropic",
        model_pattern=pattern,
        valid_from=_V2,
        input_per_million=Decimal(inp),
        output_per_million=Decimal(out),
        cached_input_per_million=Decimal(cached),
        source=_ANTHROPIC_SOURCE,
    )
    for pattern, inp, out, cached in (
        ("claude-fable-5-1*", "10", "50", "0.25"),
        ("claude-opus-5-5*", "4", "20", "0.20"),
        ("claude-sonnet-5-5*", "2", "10", "0.10"),
    )
)

# Google Gemini paid-tier list prices from ai.google.dev/gemini-api/docs/pricing, read twice on
# 2026-10-09 (the two reads agreed on these rows). The free tier costs nothing: add a $0 override
# for it. Exact model IDs, no wildcards: `gemini-3.8-flash*` would also match the differently
# priced TTS models. Not included: models priced by modality (3.1 Flash-Lite, 2.5 Flash family) and
# batch; the prompt-size-tiered Pro models came in version 2026-10-12. Flash-Lite's cache price is
# unset because the reads disagreed; unset means cached tokens are billed at the input price.
_GOOGLE_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (read 2026-10-09)"
_V3 = datetime(2027, 1, 1, tzinfo=UTC)


def _google(
    model: str,
    inp: str,
    out: str,
    cached: str | None,
    *,
    valid_from: datetime = _V2,
    valid_to: datetime | None = None,
) -> PriceEntry:
    return PriceEntry(
        pricing_version=ANTHROPIC_VERSION,
        provider="google",
        model_pattern=model,
        valid_from=valid_from,
        valid_to=valid_to,
        input_per_million=Decimal(inp),
        output_per_million=Decimal(out),
        cached_input_per_million=Decimal(cached) if cached is not None else None,
        source=_GOOGLE_SOURCE,
    )


GOOGLE_ENTRIES: tuple[PriceEntry, ...] = (
    # Introductory price through 2026-12-31, doubled from 2027-01-01.
    _google("gemini-3.8-flash", "0.75", "3.75", "0.075", valid_to=_V3),
    _google("gemini-3.8-flash", "1.50", "7.50", "0.15", valid_from=_V3),
    _google("gemini-3.5-flash", "1.50", "9.00", "0.15"),
    _google("gemini-3.5-flash-lite", "0.30", "2.50", None),
)

# Version 2026-10-12 (KI-050): prompt-size tiers and OpenAI. Each figure below was read twice on
# 2026-10-12 from the vendor's own pages and the reads agreed (docs/research/
# openai-and-tiered-prices-2026-10-12.md has the table). A tier row prices the whole request at its
# rates once the prompt (cached tokens included) is over the threshold, as the vendors state it.
# Not modelled: batch and flex (50%), fast mode (2x), regional processing (+10%), cache writes,
# modality prices. Exact model IDs only: a dated snapshot or a sibling needs its own row.
TIERED_VERSION = "2026-10-12"
_V4 = datetime(2026, 10, 12, tzinfo=UTC)
_OPENAI_SOURCE = (
    "https://developers.openai.com/api/docs/pricing and /api/docs/models/<id> (read 2026-10-12)"
)
_OPENAI_PRO_SOURCE = (
    "https://developers.openai.com/api/docs/pricing (read twice 2026-10-12; no model page)"
)
_GPT_5_6_SOL_SOURCE = (
    f"{_OPENAI_SOURCE}; promotional pricing, available at least through 2026-11-21"
)
_HAIKU_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/pricing and https://claude.com/pricing "
    "(read 2026-10-12)"
)
_GOOGLE_TIER_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (read twice 2026-10-12)"
_Rates = tuple[str, str | None, str]  # input, cached input (None: not offered), output


def _tiered(
    provider: str,
    model: str,
    base: _Rates,
    source: str,
    *,
    over: tuple[int, _Rates] | None = None,
) -> tuple[PriceEntry, ...]:
    def row(rates: _Rates, min_prompt_tokens: int | None) -> PriceEntry:
        inp, cached, out = rates
        return PriceEntry(
            pricing_version=TIERED_VERSION,
            provider=provider,
            model_pattern=model,
            valid_from=_V4,
            input_per_million=Decimal(inp),
            output_per_million=Decimal(out),
            cached_input_per_million=Decimal(cached) if cached is not None else None,
            source=source,
            min_prompt_tokens=min_prompt_tokens,
        )

    rows = [row(base, None)]
    if over is not None:
        rows.append(row(over[1], over[0]))
    return tuple(rows)


_LONG_272K = 272_000  # "more than 272K input tokens" (OpenAI model pages)

# (model, short-context rates, long-context rates or None, source)
_OPENAI_MODELS: tuple[tuple[str, _Rates, _Rates | None, str], ...] = (
    ("gpt-6-astra", ("10", "1", "50"), ("20", "2", "75"), _OPENAI_SOURCE),
    ("gpt-6.1-sol", ("2", "0.10", "10"), ("4", "0.20", "15"), _OPENAI_SOURCE),
    ("gpt-6-luna", ("0.10", "0.01", "0.50"), ("0.20", "0.02", "0.75"), _OPENAI_SOURCE),
    ("gpt-5.6-sol", ("4", "0.40", "20"), ("8", "0.80", "30"), _GPT_5_6_SOL_SOURCE),
    ("gpt-5.5", ("5", "0.50", "30"), ("10", "1", "45"), _OPENAI_SOURCE),
    ("gpt-5.4", ("2.50", "0.25", "15"), ("5", "0.50", "22.50"), _OPENAI_SOURCE),
    ("gpt-5.4-pro", ("30", None, "180"), ("60", None, "270"), _OPENAI_SOURCE),
    ("gpt-5.4-mini", ("0.75", "0.075", "4.50"), None, _OPENAI_SOURCE),
    ("gpt-5.4-nano", ("0.20", "0.02", "1.25"), None, _OPENAI_SOURCE),
    ("gpt-5.3-codex", ("1.75", "0.175", "14"), None, _OPENAI_SOURCE),
    ("gpt-5.2", ("1.75", "0.175", "14"), None, _OPENAI_SOURCE),
    ("gpt-5.2-pro", ("21", None, "168"), None, _OPENAI_SOURCE),
    ("gpt-5.1", ("1.25", "0.125", "10"), None, _OPENAI_SOURCE),
    ("gpt-5", ("1.25", "0.125", "10"), None, _OPENAI_SOURCE),
    ("gpt-5-mini", ("0.25", "0.025", "2"), None, _OPENAI_SOURCE),
    ("gpt-5-nano", ("0.05", "0.005", "0.40"), None, _OPENAI_SOURCE),
    ("gpt-5-pro", ("15", None, "120"), None, _OPENAI_SOURCE),
    ("gpt-4.1", ("2", "0.50", "8"), None, _OPENAI_SOURCE),
    ("gpt-4.1-mini", ("0.40", "0.10", "1.60"), None, _OPENAI_SOURCE),
    ("gpt-4.1-nano", ("0.10", "0.025", "0.40"), None, _OPENAI_SOURCE),
    ("gpt-4o", ("2.50", "1.25", "10"), None, _OPENAI_SOURCE),
    ("gpt-4o-mini", ("0.15", "0.075", "0.60"), None, _OPENAI_SOURCE),
    ("gpt-4o-2024-05-13", ("5", None, "15"), None, _OPENAI_PRO_SOURCE),
    ("gpt-4-turbo-2024-04-09", ("10", None, "30"), None, _OPENAI_PRO_SOURCE),
    ("gpt-4-0613", ("30", None, "60"), None, _OPENAI_PRO_SOURCE),
    ("gpt-3.5-turbo", ("0.50", None, "1.50"), None, _OPENAI_SOURCE),
    ("gpt-3.5-turbo-0125", ("0.50", None, "1.50"), None, _OPENAI_PRO_SOURCE),
    ("gpt-3.5-turbo-1106", ("1", None, "2"), None, _OPENAI_PRO_SOURCE),
    ("gpt-3.5-turbo-instruct", ("1.50", None, "2"), None, _OPENAI_SOURCE),
)

TIERED_ENTRIES: tuple[PriceEntry, ...] = (
    *(
        row
        for model, short, long_, source in _OPENAI_MODELS
        for row in _tiered(
            "openai",
            model,
            short,
            source,
            over=(_LONG_272K, long_) if long_ is not None else None,
        )
    ),
    # Haiku 5.5: prompts over 100,000 tokens (cache reads and writes counted) pay the higher rate.
    *_tiered(
        "anthropic",
        "claude-haiku-5-5*",
        ("0.10", "0.01", "0.50"),
        _HAIKU_SOURCE,
        over=(100_000, ("0.50", "0.05", "2.50")),
    ),
    # Gemini Pro models: prompts over 200k tokens; the output price includes thinking tokens.
    *_tiered(
        "google",
        "gemini-3.1-pro-preview",
        ("2", "0.20", "12"),
        _GOOGLE_TIER_SOURCE,
        over=(200_000, ("4", "0.40", "18")),
    ),
    *_tiered(
        "google",
        "gemini-3.1-pro-preview-customtools",
        ("2", "0.20", "12"),
        _GOOGLE_TIER_SOURCE,
        over=(200_000, ("4", "0.40", "18")),
    ),
    *_tiered(
        "google",
        "gemini-2.5-pro",
        ("1.25", "0.125", "10"),
        _GOOGLE_TIER_SOURCE,
        over=(200_000, ("2.50", "0.25", "15")),
    ),
)


# Version 2026-10-12b (KI-050, ADR-044): billing modifiers and the models the earlier batches left
# out. It re-publishes every OpenAI and Anthropic standard row (same prices as 2026-10-12, now with
# cache-write and regional prices) beside the Batch, Flex, Fast and Ultrafast rows the vendors
# state; the later version wins a tie, so the earlier rows stay as published. Each figure was read
# twice on 2026-10-12 (docs/research/openai-and-tiered-prices-2026-10-12.md, second table). A price
# is entered only where the page states it: a tier or a regional endpoint a page does not price
# leaves the call unpriced with a reason (engine.py), never charged the standard rate.
BATCH2_VERSION = "2026-10-12b"
_RateRow = tuple[str, str | None, str | None, str]  # input, cached input, cache write, output
_OPENAI_V2_ROWS: tuple[tuple[str, str, _RateRow, _RateRow | None], ...] = (
    (
        "gpt-6-astra",
        "standard",
        ("10.00", "1.00", "12.50", "50.00"),
        ("20.00", "2.00", "25.00", "75.00"),
    ),
    (
        "gpt-6.1-sol",
        "standard",
        ("2.00", "0.10", "2.50", "10.00"),
        ("4.00", "0.20", "5.00", "15.00"),
    ),
    ("gpt-6-luna", "standard", ("0.10", "0.01", "0.125", "0.50"), ("0.20", "0.02", "0.25", "0.75")),
    ("gpt-6-sol", "standard", ("2.00", "0.20", "2.50", "10.00"), ("4.00", "0.40", "5.00", "15.00")),
    (
        "gpt-5.6-sol",
        "standard",
        ("4.00", "0.40", "5.00", "20.00"),
        ("8.00", "0.80", "10.00", "30.00"),
    ),
    (
        "gpt-5.6-terra",
        "standard",
        ("2.00", "0.20", "2.50", "12.00"),
        ("4.00", "0.40", "5.00", "18.00"),
    ),
    (
        "gpt-5.6-luna",
        "standard",
        ("0.20", "0.02", "0.25", "1.20"),
        ("0.40", "0.04", "0.50", "1.80"),
    ),
    ("gpt-5.5", "standard", ("5.00", "0.50", None, "30.00"), ("10.00", "1.00", None, "45.00")),
    ("gpt-5.4", "standard", ("2.50", "0.25", None, "15.00"), ("5.00", "0.50", None, "22.50")),
    ("gpt-5.4-pro", "standard", ("30.00", None, None, "180.00"), ("60.00", None, None, "270.00")),
    ("gpt-5.4-mini", "standard", ("0.75", "0.075", None, "4.50"), None),
    ("gpt-5.4-nano", "standard", ("0.20", "0.02", None, "1.25"), None),
    ("gpt-5.2", "standard", ("1.75", "0.175", None, "14.00"), None),
    ("gpt-5.2-pro", "standard", ("21.00", None, None, "168.00"), None),
    ("gpt-5.1", "standard", ("1.25", "0.125", None, "10.00"), None),
    ("gpt-5", "standard", ("1.25", "0.125", None, "10.00"), None),
    ("gpt-5-mini", "standard", ("0.25", "0.025", None, "2.00"), None),
    ("gpt-5-nano", "standard", ("0.05", "0.005", None, "0.40"), None),
    ("gpt-5-pro", "standard", ("15.00", None, None, "120.00"), None),
    ("gpt-4.1", "standard", ("2.00", "0.50", None, "8.00"), None),
    ("gpt-4.1-mini", "standard", ("0.40", "0.10", None, "1.60"), None),
    ("gpt-4.1-nano", "standard", ("0.10", "0.025", None, "0.40"), None),
    ("gpt-4o", "standard", ("2.50", "1.25", None, "10.00"), None),
    ("gpt-4o-mini", "standard", ("0.15", "0.075", None, "0.60"), None),
    ("o4-mini", "standard", ("1.10", "0.275", None, "4.40"), None),
    ("o3", "standard", ("2.00", "0.50", None, "8.00"), None),
    ("o3-mini", "standard", ("1.10", "0.55", None, "4.40"), None),
    ("o3-pro", "standard", ("20.00", None, None, "80.00"), None),
    ("o1", "standard", ("15.00", "7.50", None, "60.00"), None),
    ("o1-pro", "standard", ("150.00", None, None, "600.00"), None),
    ("gpt-4o-2024-05-13", "standard", ("5.00", None, None, "15.00"), None),
    ("gpt-4-turbo-2024-04-09", "standard", ("10.00", None, None, "30.00"), None),
    ("gpt-4-0613", "standard", ("30.00", None, None, "60.00"), None),
    ("gpt-3.5-turbo", "standard", ("0.50", None, None, "1.50"), None),
    ("gpt-3.5-turbo-0125", "standard", ("0.50", None, None, "1.50"), None),
    (
        "gpt-6-astra",
        "batch",
        ("5.00", "0.50", "6.25", "25.00"),
        ("10.00", "1.00", "12.50", "37.50"),
    ),
    ("gpt-6.1-sol", "batch", ("1.00", "0.05", "1.25", "5.00"), ("2.00", "0.10", "2.50", "7.50")),
    (
        "gpt-6-luna",
        "batch",
        ("0.05", "0.005", "0.0625", "0.25"),
        ("0.10", "0.01", "0.125", "0.375"),
    ),
    ("gpt-6-sol", "batch", ("1.00", "0.10", "1.25", "5.00"), ("2.00", "0.20", "2.50", "7.50")),
    ("gpt-5.6-sol", "batch", ("2.00", "0.20", "2.50", "10.00"), ("4.00", "0.40", "5.00", "15.00")),
    ("gpt-5.6-terra", "batch", ("1.00", "0.10", "1.25", "6.00"), ("2.00", "0.20", "2.50", "9.00")),
    ("gpt-5.6-luna", "batch", ("0.10", "0.01", "0.125", "0.60"), ("0.20", "0.02", "0.25", "0.90")),
    ("gpt-5.5", "batch", ("2.50", "0.25", None, "15.00"), ("5.00", "0.50", None, "22.50")),
    ("gpt-5.4", "batch", ("1.25", "0.13", None, "7.50"), ("2.50", "0.25", None, "11.25")),
    ("gpt-5.4-pro", "batch", ("15.00", None, None, "90.00"), ("30.00", None, None, "135.00")),
    ("gpt-5.4-mini", "batch", ("0.375", "0.0375", None, "2.25"), None),
    ("gpt-5.4-nano", "batch", ("0.10", "0.01", None, "0.625"), None),
    ("gpt-5.2", "batch", ("0.875", "0.0875", None, "7.00"), None),
    ("gpt-5.2-pro", "batch", ("10.50", None, None, "84.00"), None),
    ("gpt-5.1", "batch", ("0.625", "0.0625", None, "5.00"), None),
    ("gpt-5", "batch", ("0.625", "0.0625", None, "5.00"), None),
    ("gpt-5-mini", "batch", ("0.125", "0.0125", None, "1.00"), None),
    ("gpt-5-nano", "batch", ("0.025", "0.0025", None, "0.20"), None),
    ("gpt-5-pro", "batch", ("7.50", None, None, "60.00"), None),
    ("gpt-4.1", "batch", ("1.00", None, None, "4.00"), None),
    ("gpt-4.1-mini", "batch", ("0.20", None, None, "0.80"), None),
    ("gpt-4.1-nano", "batch", ("0.05", None, None, "0.20"), None),
    ("gpt-4o", "batch", ("1.25", None, None, "5.00"), None),
    ("gpt-4o-mini", "batch", ("0.075", None, None, "0.30"), None),
    ("o4-mini", "batch", ("0.55", None, None, "2.20"), None),
    ("o3", "batch", ("1.00", None, None, "4.00"), None),
    ("o3-mini", "batch", ("0.55", None, None, "2.20"), None),
    ("o3-pro", "batch", ("10.00", None, None, "40.00"), None),
    ("o1", "batch", ("7.50", None, None, "30.00"), None),
    ("o1-pro", "batch", ("75.00", None, None, "300.00"), None),
    ("gpt-4o-2024-05-13", "batch", ("2.50", None, None, "7.50"), None),
    ("gpt-4-turbo-2024-04-09", "batch", ("5.00", None, None, "15.00"), None),
    ("gpt-4-0613", "batch", ("15.00", None, None, "30.00"), None),
    ("gpt-3.5-turbo-0125", "batch", ("0.25", None, None, "0.75"), None),
    ("gpt-6-astra", "flex", ("5.00", "0.50", "6.25", "25.00"), ("10.00", "1.00", "12.50", "37.50")),
    ("gpt-6.1-sol", "flex", ("1.00", "0.05", "1.25", "5.00"), ("2.00", "0.10", "2.50", "7.50")),
    ("gpt-6-luna", "flex", ("0.05", "0.005", "0.0625", "0.25"), ("0.10", "0.01", "0.125", "0.375")),
    ("gpt-6-sol", "flex", ("1.00", "0.10", "1.25", "5.00"), ("2.00", "0.20", "2.50", "7.50")),
    ("gpt-5.6-sol", "flex", ("2.00", "0.20", "2.50", "10.00"), ("4.00", "0.40", "5.00", "15.00")),
    ("gpt-5.6-terra", "flex", ("1.00", "0.10", "1.25", "6.00"), ("2.00", "0.20", "2.50", "9.00")),
    ("gpt-5.6-luna", "flex", ("0.10", "0.01", "0.125", "0.60"), ("0.20", "0.02", "0.25", "0.90")),
    ("gpt-5.5", "flex", ("2.50", "0.25", None, "15.00"), ("5.00", "0.50", None, "22.50")),
    ("gpt-5.4", "flex", ("1.25", "0.13", None, "7.50"), ("2.50", "0.25", None, "11.25")),
    ("gpt-5.4-pro", "flex", ("15.00", None, None, "90.00"), ("30.00", None, None, "135.00")),
    ("gpt-5.4-mini", "flex", ("0.375", "0.0375", None, "2.25"), None),
    ("gpt-5.4-nano", "flex", ("0.10", "0.01", None, "0.625"), None),
    ("gpt-5.2", "flex", ("0.875", "0.0875", None, "7.00"), None),
    ("gpt-5.1", "flex", ("0.625", "0.0625", None, "5.00"), None),
    ("gpt-5", "flex", ("0.625", "0.0625", None, "5.00"), None),
    ("gpt-5-mini", "flex", ("0.125", "0.0125", None, "1.00"), None),
    ("gpt-5-nano", "flex", ("0.025", "0.0025", None, "0.20"), None),
    ("o4-mini", "flex", ("0.55", "0.138", None, "2.20"), None),
    ("o3", "flex", ("1.00", "0.25", None, "4.00"), None),
    (
        "gpt-6-astra",
        "fast",
        ("20.00", "2.00", "25.00", "100.00"),
        ("40.00", "4.00", "50.00", "150.00"),
    ),
    ("gpt-6.1-sol", "fast", ("4.00", "0.20", "5.00", "20.00"), ("8.00", "0.40", "10.00", "30.00")),
    ("gpt-6-luna", "fast", ("0.20", "0.02", "0.25", "1.00"), ("0.40", "0.04", "0.50", "1.50")),
    ("gpt-6-sol", "fast", ("4.00", "0.40", "5.00", "20.00"), ("8.00", "0.80", "10.00", "30.00")),
    (
        "gpt-5.6-sol",
        "fast",
        ("8.00", "0.80", "10.00", "40.00"),
        ("16.00", "1.60", "20.00", "60.00"),
    ),
    (
        "gpt-5.6-terra",
        "fast",
        ("4.00", "0.40", "5.00", "24.00"),
        ("8.00", "0.80", "10.00", "36.00"),
    ),
    ("gpt-5.6-luna", "fast", ("0.40", "0.04", "0.50", "2.40"), ("0.80", "0.08", "1.00", "3.60")),
    ("gpt-5.5", "fast", ("12.50", "1.25", None, "75.00"), None),
    ("gpt-5.4", "fast", ("5.00", "0.50", None, "30.00"), None),
    ("gpt-5.4-mini", "fast", ("1.50", "0.15", None, "9.00"), None),
    ("gpt-5.2", "fast", ("3.50", "0.35", None, "28.00"), None),
    ("gpt-5.1", "fast", ("2.50", "0.25", None, "20.00"), None),
    ("gpt-5", "fast", ("2.50", "0.25", None, "20.00"), None),
    ("gpt-5-mini", "fast", ("0.45", "0.045", None, "3.60"), None),
    ("gpt-4.1", "fast", ("3.50", "0.875", None, "14.00"), None),
    ("gpt-4.1-mini", "fast", ("0.70", "0.175", None, "2.80"), None),
    ("gpt-4.1-nano", "fast", ("0.20", "0.05", None, "0.80"), None),
    ("gpt-4o", "fast", ("4.25", "2.125", None, "17.00"), None),
    ("gpt-4o-mini", "fast", ("0.25", "0.125", None, "1.00"), None),
    ("o4-mini", "fast", ("2.00", "0.50", None, "8.00"), None),
    ("o3", "fast", ("3.50", "0.875", None, "14.00"), None),
    ("gpt-4o-2024-05-13", "fast", ("8.75", None, None, "26.25"), None),
    (
        "gpt-6-astra",
        "ultrafast",
        ("60.00", "6.00", "75.00", "300.00"),
        ("120.00", "12.00", "150.00", "450.00"),
    ),
    (
        "gpt-6.1-sol",
        "ultrafast",
        ("12.00", "0.60", "15.00", "60.00"),
        ("24.00", "1.20", "30.00", "90.00"),
    ),
    # Specialized models table of the pricing page: Standard and Fast tabs only.
    ("gpt-5.3-codex", "standard", ("1.75", "0.175", None, "14.00"), None),
    ("gpt-5.3-codex", "fast", ("3.50", "0.35", None, "28.00"), None),
)

# Models whose own page states the 10% regional-processing uplift (not the generic pricing-page
# sentence). It is entered for the Standard tier only: no page says how it combines with the others.
_OPENAI_REGIONAL = frozenset(
    {
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    }
)
_OPENAI_NO_PAGE = frozenset(
    {"gpt-4o-2024-05-13", "gpt-4-turbo-2024-04-09", "gpt-4-0613", "gpt-3.5-turbo-0125"}
)
# developers.openai.com/api/docs/deprecations, read twice 2026-10-12: davinci-002, babbage-002,
# gpt-3.5-turbo-1106 and gpt-3.5-turbo-instruct were shut down on 2026-09-28 and are not entered
# here (the earlier batch published the last two and cannot be edited); these stop on 2026-10-23.
_OPENAI_SHUTDOWN = datetime(2026, 10, 23, tzinfo=UTC)
_OPENAI_SHUTS_DOWN = frozenset(
    {
        "gpt-3.5-turbo",
        "gpt-3.5-turbo-0125",
        "gpt-4o-2024-05-13",
        "gpt-4-turbo-2024-04-09",
        "gpt-4-0613",
        "o1",
        "o1-pro",
        "o3-mini",
        "o4-mini",
    }
)
_REGIONAL_UPLIFT = Decimal("1.1")
_OPENAI_PROMO = "; promotional pricing, available at least through 2026-11-21"


def _price(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _openai_v2() -> tuple[PriceEntry, ...]:
    has_long = {m for m, tier, _, long_ in _OPENAI_V2_ROWS if tier == "standard" and long_}
    entries: list[PriceEntry] = []
    for model, tier, short, long_ in _OPENAI_V2_ROWS:
        if tier == "standard":
            source = _OPENAI_PRO_SOURCE if model in _OPENAI_NO_PAGE else _OPENAI_SOURCE
        else:
            source = (
                f"https://developers.openai.com/api/docs/pricing, {tier.capitalize()} tab "
                "(read twice 2026-10-12)"
            )
        if model == "gpt-5.6-sol":
            source += _OPENAI_PROMO
        valid_to = _OPENAI_SHUTDOWN if model in _OPENAI_SHUTS_DOWN else None
        if valid_to is not None:
            source += "; shutdown 2026-10-23 (deprecations page, read twice 2026-10-12)"
        regional = _REGIONAL_UPLIFT if tier == "standard" and model in _OPENAI_REGIONAL else None
        # A tier whose long-context column is "-" has no price above 272K: leave those unpriced.
        ceiling = _LONG_272K if long_ is None and model in has_long else None
        for rates, over in ((short, None), (long_, _LONG_272K)):
            if rates is None:
                continue
            inp, cached, write, out = rates
            entries.append(
                PriceEntry(
                    pricing_version=BATCH2_VERSION,
                    provider="openai",
                    model_pattern=model,
                    valid_from=_V4,
                    valid_to=valid_to,
                    input_per_million=Decimal(inp),
                    output_per_million=Decimal(out),
                    cached_input_per_million=_price(cached),
                    source=source,
                    min_prompt_tokens=over,
                    max_prompt_tokens=ceiling if over is None else None,
                    service_tier=tier,
                    cache_write_per_million=_price(write),
                    regional_multiplier=regional,
                )
            )
    return tuple(entries)


# Anthropic (platform.claude.com/docs/en/about-claude/pricing and /build-with-claude/fast-mode,
# /manage-claude/data-residency, read twice 2026-10-12: rendered table and raw markdown). The page
# states: Batch is 50% of every price, cache writes are 1.25x (5 minutes) and 2x (1 hour) the input
# price, Fast mode is 2x for the three Opus models listed and stacks with caching and data
# residency, and US-only inference is 1.1x on every category for Claude 4.6 and later (stacking with
# batch, caching and fast). Retired models (Opus 4.1 and 4, Sonnet 4, Haiku 3.5) are left out.
_ANTHROPIC_V2_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/pricing, "
    "/build-with-claude/fast-mode and /manage-claude/data-residency (read twice 2026-10-12)"
)
_HALF = Decimal("0.5")
_TWICE = Decimal(2)
# (patterns, input, output, cache read, 5m write, 1h write, 4.6-or-later, fast mode)
_ANTHROPIC_V2_MODELS: tuple[tuple[tuple[str, ...], str, str, str, str, str, bool, bool], ...] = (
    (("claude-fable-5-1*",), "10", "50", "0.25", "12.50", "20", True, False),
    (("claude-mythos-5-1*",), "10", "50", "0.25", "12.50", "20", True, False),
    (("claude-fable-5", "claude-fable-5-20*"), "10", "50", "1", "12.50", "20", True, False),
    (("claude-mythos-5", "claude-mythos-5-20*"), "10", "50", "1", "12.50", "20", True, False),
    (("claude-opus-5-5*",), "4", "20", "0.20", "5", "8", True, True),
    (("claude-opus-5", "claude-opus-5-20*"), "5", "25", "0.50", "6.25", "10", True, True),
    (("claude-opus-4-8*",), "5", "25", "0.50", "6.25", "10", True, True),
    (("claude-opus-4-7*",), "5", "25", "0.50", "6.25", "10", True, False),
    (("claude-opus-4-6*",), "5", "25", "0.50", "6.25", "10", True, False),
    (("claude-opus-4-5*",), "5", "25", "0.50", "6.25", "10", False, False),
    (("claude-sonnet-5-5*",), "2", "10", "0.10", "2.50", "4", True, False),
    (("claude-sonnet-5", "claude-sonnet-5-20*"), "2", "10", "0.20", "2.50", "4", True, False),
    (("claude-sonnet-4-6*",), "3", "15", "0.30", "3.75", "6", True, False),
    (("claude-sonnet-4-5*",), "3", "15", "0.30", "3.75", "6", False, False),
    (("claude-haiku-4-5*",), "1", "5", "0.10", "1.25", "2", False, False),
)
# Haiku 5.5: the prompt-size tier is part of every tier (the page prices both sets in each).
_HAIKU_5_5 = (
    (None, ("0.10", "0.50", "0.01", "0.125", "0.20")),
    (100_000, ("0.50", "2.50", "0.05", "0.625", "1")),
)


def _anthropic_v2() -> tuple[PriceEntry, ...]:
    entries: list[PriceEntry] = []

    def add(
        pattern: str,
        rates: tuple[str, str, str, str, str],
        over: int | None,
        regional: bool,
        fast: bool,
    ) -> None:
        inp, out, read, write5, write1h = (Decimal(v) for v in rates)
        # Batch halves every price; Fast mode doubles them; neither applies to the other.
        for tier, scale in (("standard", Decimal(1)), ("batch", _HALF)) + (
            (("fast", _TWICE),) if fast else ()
        ):
            entries.append(
                PriceEntry(
                    pricing_version=BATCH2_VERSION,
                    provider="anthropic",
                    model_pattern=pattern,
                    valid_from=_V4,
                    input_per_million=inp * scale,
                    output_per_million=out * scale,
                    cached_input_per_million=read * scale,
                    source=_ANTHROPIC_V2_SOURCE,
                    min_prompt_tokens=over,
                    service_tier=tier,
                    cache_write_per_million=write5 * scale,
                    cache_write_1h_per_million=write1h * scale,
                    regional_multiplier=_REGIONAL_UPLIFT if regional else None,
                )
            )

    for patterns, inp, out, read, write5, write1h, regional, fast in _ANTHROPIC_V2_MODELS:
        for pattern in patterns:
            add(pattern, (inp, out, read, write5, write1h), None, regional, fast)
    for over, (inp, out, read, write5, write1h) in _HAIKU_5_5:
        add("claude-haiku-5-5*", (inp, out, read, write5, write1h), over, True, False)
    return tuple(entries)


# Gemini rows priced by modality: the page states one price for text, image and video input and a
# higher one for audio input. Only the text price is entered, so audio input is under-estimated.
_GOOGLE_V2_SOURCE = (
    "https://ai.google.dev/gemini-api/docs/pricing (read twice 2026-10-12; text, image and video "
    "input price, audio input is priced higher and not modelled)"
)


def _google_v2(
    model: str,
    inp: str,
    out: str,
    cached: str,
    *,
    valid_from: datetime = _V4,
    valid_to: datetime | None = None,
) -> PriceEntry:
    return PriceEntry(
        pricing_version=BATCH2_VERSION,
        provider="google",
        model_pattern=model,
        valid_from=valid_from,
        valid_to=valid_to,
        input_per_million=Decimal(inp),
        output_per_million=Decimal(out),
        cached_input_per_million=Decimal(cached),
        source=_GOOGLE_V2_SOURCE,
    )


GOOGLE_V2_ENTRIES: tuple[PriceEntry, ...] = (
    # Introductory price through 2026-12-31, doubled from 2027-01-01 (as for gemini-3.8-flash).
    _google_v2("gemini-3.6-flash", "0.75", "3.75", "0.075", valid_to=_V3),
    _google_v2("gemini-3.6-flash", "1.50", "7.50", "0.15", valid_from=_V3),
    _google_v2("gemini-3.1-flash-lite", "0.25", "1.50", "0.025"),
    _google_v2("gemini-3-flash-preview", "0.50", "3.00", "0.05"),
    _google_v2("gemini-2.5-flash", "0.30", "2.50", "0.03"),
    _google_v2("gemini-2.5-flash-lite", "0.10", "0.40", "0.01"),
    # Correction of version 2026-10-09, which left this cache price unset (cached tokens were
    # billed at the input price). The page states $0.03 for every modality, read twice
    # 2026-10-12. Same start date as the row it corrects, so the later version wins from then on.
    PriceEntry(
        pricing_version=BATCH2_VERSION,
        provider="google",
        model_pattern="gemini-3.5-flash-lite",
        valid_from=_V2,
        input_per_million=Decimal("0.30"),
        output_per_million=Decimal("2.50"),
        cached_input_per_million=Decimal("0.03"),
        source="https://ai.google.dev/gemini-api/docs/pricing (read twice 2026-10-12)",
    ),
)

BATCH2_ENTRIES: tuple[PriceEntry, ...] = (
    *_openai_v2(),
    *_anthropic_v2(),
    *GOOGLE_V2_ENTRIES,
)

BUILTIN_ENTRIES: tuple[PriceEntry, ...] = (
    *_EXAMPLE_ENTRIES,
    *ANTHROPIC_ENTRIES,
    *GOOGLE_ENTRIES,
    *TIERED_ENTRIES,
    *BATCH2_ENTRIES,
)


def builtin_price_book() -> PriceBook:
    return PriceBook(BUILTIN_ENTRIES)
