"""The built-in price table. Illustrative entries only (ADR-040, KI-050).

Real vendor prices cannot be verified offline and change often. A wrong default price would make
every dashboard figure quietly wrong, so this table contains the documentation/fixture models only;
real models are priced by workspace overrides or by a reviewed new `pricing_version` added here.
Never edit a published version's prices: add a new version with a later `valid_from`.
"""

from datetime import UTC, datetime
from decimal import Decimal

from abb_api.cost.pricing import PriceBook, PriceEntry

BUILTIN_VERSION = "2026-10-01"

_V1 = datetime(2026, 10, 1, tzinfo=UTC)

BUILTIN_ENTRIES: tuple[PriceEntry, ...] = (
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


def builtin_price_book() -> PriceBook:
    return PriceBook(BUILTIN_ENTRIES)
