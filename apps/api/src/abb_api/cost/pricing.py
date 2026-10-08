"""Versioned price entries and the lookup that selects one (spec §79.1, ADR-040).

Entries are immutable data. Selection depends only on the entry set, the model, the provider and
the event time, never on the wall clock, so recomputing history later gives the same answer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatchcase

ZERO = Decimal(0)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class PriceEntry:
    pricing_version: str
    model_pattern: str  # glob, case-insensitive (`*` and `?`)
    valid_from: datetime
    input_per_million: Decimal
    output_per_million: Decimal
    provider: str | None = None  # None: any provider
    valid_to: datetime | None = None  # exclusive
    cached_input_per_million: Decimal | None = None  # None: cached tokens cost the input price
    request_price: Decimal = ZERO
    currency: str = "USD"
    source: str = ""
    origin: str = "builtin"  # "builtin" | "override"
    project_id: str | None = None  # overrides only: None = whole workspace
    created_at: datetime | None = None  # overrides only: the newer of two equal rows wins

    def applies(self, provider: str | None, model: str, at: datetime) -> bool:
        if self.valid_from > at or (self.valid_to is not None and at >= self.valid_to):
            return False
        if self.provider is not None and (provider or "").lower() != self.provider.lower():
            return False
        return fnmatchcase(model.lower(), self.model_pattern.lower())

    def specificity(self) -> tuple[int, int, int, int, datetime, datetime]:
        """Bigger wins: override, project scope, provider-specific, longer literal, newer start,
        newer row (so a correction with the same pattern and start replaces the earlier one)."""
        literal = sum(1 for ch in self.model_pattern if ch not in "*?")
        return (
            1 if self.origin == "override" else 0,
            1 if self.project_id is not None else 0,
            1 if self.provider is not None else 0,
            literal,
            self.valid_from,
            self.created_at or _EPOCH,
        )


class PriceBook:
    """A set of entries. `find` returns the single entry that prices a call, or None."""

    def __init__(self, entries: tuple[PriceEntry, ...] | list[PriceEntry]) -> None:
        self._entries = tuple(entries)

    @property
    def entries(self) -> tuple[PriceEntry, ...]:
        return self._entries

    def find(
        self,
        provider: str | None,
        model: str,
        at: datetime,
        *,
        pricing_version: str | None = None,
    ) -> PriceEntry | None:
        """`pricing_version` pins built-in entries to one version (reproducing an old calculation).

        Overrides carry their own `override:<id>` version, so a pin selects them the same way."""
        candidates = [
            e
            for e in self._entries
            if e.applies(provider, model, at)
            and (pricing_version is None or e.pricing_version == pricing_version)
        ]
        return max(candidates, key=PriceEntry.specificity, default=None)
