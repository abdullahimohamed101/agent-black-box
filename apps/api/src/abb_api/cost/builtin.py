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
# Not included: Haiku 5.5 (the page tiers its price at 100K prompt tokens, which the engine cannot
# express), legacy models, and every OpenAI model (the vendor page could not be read here; only
# third-party figures were available).
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
# priced TTS models. Not included: models priced by prompt size or by modality (3.1 Pro, 3.1
# Flash-Lite, 2.5 family) and batch. Flash-Lite's cache price is unset because the reads
# disagreed; unset means cached tokens are billed at the input price.
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

BUILTIN_ENTRIES: tuple[PriceEntry, ...] = (*_EXAMPLE_ENTRIES, *ANTHROPIC_ENTRIES, *GOOGLE_ENTRIES)


def builtin_price_book() -> PriceBook:
    return PriceBook(BUILTIN_ENTRIES)
