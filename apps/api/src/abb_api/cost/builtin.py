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

BUILTIN_ENTRIES: tuple[PriceEntry, ...] = (*_EXAMPLE_ENTRIES, *ANTHROPIC_ENTRIES)


def builtin_price_book() -> PriceBook:
    return PriceBook(BUILTIN_ENTRIES)
