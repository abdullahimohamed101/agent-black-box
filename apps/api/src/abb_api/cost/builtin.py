"""The built-in price table (ADR-040, KI-050).

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

BUILTIN_ENTRIES: tuple[PriceEntry, ...] = (
    *_EXAMPLE_ENTRIES,
    *ANTHROPIC_ENTRIES,
    *GOOGLE_ENTRIES,
    *TIERED_ENTRIES,
)


def builtin_price_book() -> PriceBook:
    return PriceBook(BUILTIN_ENTRIES)
